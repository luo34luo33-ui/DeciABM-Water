import asyncio
import copy

import httpx

from .config import WATER_PER_MU
from .economics import affordability, aggregate, context, credit_profile, distribute_relief, settle_farmer, water
from .fallback_rules import bank_rule, bureau_rule, farmer_rule
from .jev_client import farmer_facts
from .llm_client import ModelCalls


def match_quotas(bureaus, decisions):
    offers = {bid: -d["quota_delta_m3"] for bid, d in decisions.items() if d["quota_delta_m3"] < 0}
    requests = {bid: d["quota_delta_m3"] for bid, d in decisions.items() if d["quota_delta_m3"] > 0}
    supply, demand = sum(offers.values()), sum(requests.values())
    for b in bureaus.values():
        b["quota_delta_m3"] = 0.
    if min(supply, demand) <= 0:
        return []
    volume = min(supply, demand)
    sellers = [[bid, q * volume / supply] for bid, q in sorted(offers.items())]
    buyers = [[bid, q * volume / demand] for bid, q in sorted(requests.items())]
    transfers = []
    i = j = 0
    while i < len(sellers) and j < len(buyers):
        seller, amount_s = sellers[i]
        buyer, amount_b = buyers[j]
        amount = min(amount_s, amount_b)
        transfers.append({"id": f"transfer-{len(transfers)+1}", "from": seller, "to": buyer, "volume_m3": amount,
                          "offered_m3": offers[seller], "requested_m3": requests[buyer]})
        bureaus[seller]["quota_delta_m3"] -= amount
        bureaus[buyer]["quota_delta_m3"] += amount
        sellers[i][1] -= amount
        buyers[j][1] -= amount
        if sellers[i][1] < 1e-7:
            i += 1
        if buyers[j][1] < 1e-7:
            j += 1
    return transfers


async def prepare_tick(current, request, progress, key="", client=None):
    state = copy.deepcopy(current)
    tick = current["tick"] + 1
    env = request.environment.model_dump()
    names = {'drought': '干旱冲击', 'campaign': '节水号召', 'subsidy': '补贴落地', 'credit': '信贷收紧', 'redline': '红线收紧'}
    active = [e for e in state['setup'].get('events', []) if e['season'] <= tick <= e['end_season']]
    for event in active:
        kind = event['kind']
        if kind == 'drought':
            env.update(drought=max(env['drought'], .4), water_price_floor=max(env['water_price_floor'], .5), redline_ratio=min(env['redline_ratio'], .65))
        elif kind == 'campaign':
            env['enthusiasm'] = max(env['enthusiasm'], .8)
        elif kind == 'subsidy':
            env.update(subsidy_floor=max(env['subsidy_floor'], .55), budget_multiplier=max(env['budget_multiplier'], 2.5))
        elif kind == 'credit':
            env['credit_factor'] = min(env['credit_factor'], .4)
        elif kind == 'redline':
            env['redline_ratio'] = min(env['redline_ratio'], .8)
    if active:
        env['scenario'] = '+'.join(e['kind'] for e in active)
        env['instruction'] += '\n本季事件：' + '、'.join(names[e['kind']] for e in active) + '。遵守本季水价与补贴下限，结合经济约束决策。'
    state.update(tick=tick, environment=env, base_environment=request.environment.model_dump(), active_events=active, mode=request.mode, traces=[])
    previous = current["farmers"]
    for f in state["farmers"].values():
        if f["status"] == "pending" and f["installation_tick"] == tick:
            f["status"] = "operating"
        f["financing"] = {}
        f['relief_cny'] = 0
    progress.update(tick=tick, phase="农户核算与融资申请", completed=0, total=0, active_entity_ids=[])
    owns_client = request.mode == "online" and client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), limits=httpx.Limits(max_connections=4))
    calls = ModelCalls(request.mode, key, progress, client)
    try:
        # Execute previously announced policy before agents act. Current event
        # constraints and actual fiscal capacity still take precedence.
        for bid, b in state['bureaus'].items():
            planned = b.get('next_policy') or b
            farmers = [f for f in state['farmers'].values() if f['bureau_id'] == bid]
            uncommitted = [f for f in farmers if f['status'] == 'uncommitted']
            b['budget_cny'] = round(b['budget_base_cny'] * env['budget_multiplier'], 2)
            b['subsidy_spent_cny'] = 0
            b['quota_delta_m3'] = 0
            quote = sum(f['equipment_cny'] for f in uncommitted)
            cap = min(.8, max(0, b['budget_cny'] - .005 * len(uncommitted)) / quote) if quote else .8
            ceiling = affordability(farmers, b, env, current['environment'])['water_price_ceiling']
            b['water_price'] = min(ceiling, max(env['water_price_floor'], planned['water_price']))
            b['subsidy_rate'] = min(cap, max(env['subsidy_floor'], planned['subsidy_rate']))
            b['applied_policy'] = {'effective_tick': tick, 'water_price': b['water_price'], 'subsidy_rate': b['subsidy_rate']}
        distribute_relief(state)
        for fid, f in state['farmers'].items():
            if f['status'] == 'uncommitted':
                c = context(f, state['bureaus'][f['bureau_id']], env, state['farmers'], previous=previous)
                f['loan_request'] = {'tick': tick, 'requested_cny': c['required_loan_cny'],
                                     'reason': '现金优先；设备投资差额自动申请贷款' if c['required_loan_cny'] > 0 else '自费可行，无贷款申请'}
        progress["phase"] = "农商行授信审批"

        async def bank_one(bank_id):
            bank = state["banks"][bank_id]
            village = state["villages"][bank["village_id"]]
            applicants = []
            for fid in village["farmers"]:
                f = state["farmers"][fid]
                if f["status"] != "uncommitted":
                    continue
                ctx = context(f, state["bureaus"][f["bureau_id"]], env, state["farmers"], previous=previous)
                if f["loan_request"]["requested_cny"] > 0:
                    applicants.append(credit_profile(f, ctx))
                else:
                    f["financing"] = {"approved": True, "amount_cny": 0, "rate": 0, "rationale": "可用现金足够，自费优先，无需银行授信。", "source": "self"}
            available = round(bank["cash_cny"] * env["credit_factor"], 2)
            facts = {"bank_id": bank_id, "season": tick, "environment": env, "available_cny": available,
                "credit_factor": env["credit_factor"], "previous_rate": bank["rate"],
                "overdue_ratio": min(1, bank["overdue_cny"] / max(1, bank["outstanding_cny"])), "applicants": applicants}
            trace = await calls.decide("bank", bank_id, facts, lambda: bank_rule(facts))
            bank.update(rate=trace["result"]["rate"], decision=trace, approvals=[])
            by_id = {a["farmer_id"]: a for a in applicants}
            for a in trace["result"]["approvals"]:
                applicant = by_id[a["farmer_id"]]
                amount = applicant["required_loan_cny"] if a["approve"] else 0
                coverage = applicant["risk_adjusted_surplus_cny"] / max(.01, applicant["required_loan_cny"] * (.2 + bank["rate"]))
                assessment = {k: applicant[k] for k in ("age", "skill", "risk_adjusted_surplus_cny", "minimum_coverage", "ability_gain_factor")}
                assessment["coverage"] = round(coverage, 4)
                approval = {**a, "amount_cny": amount, "requested_cny": applicant["required_loan_cny"],
                            "rate": bank["rate"], "source": trace["source"], "used": False, "assessment": assessment}
                bank["approvals"].append(approval)
                state["farmers"][a["farmer_id"]]["financing"] = {"approved": a["approve"], "amount_cny": amount,
                    "rate": bank["rate"], "rationale": a["rationale"], "source": trace["source"], "assessment": assessment}
        await asyncio.gather(*(bank_one(bank_id) for bank_id in state["banks"]))
        progress["phase"] = "Jev 农户采纳决策"
        contexts = {fid: context(f, state["bureaus"][f["bureau_id"]], env, state["farmers"], previous=previous)
                    for fid, f in state["farmers"].items()}

        async def farmer_one(fid):
            f, ctx = state["farmers"][fid], contexts[fid]
            facts = farmer_facts(f, ctx, tick)
            trace = await calls.decide("farmer", fid, facts, lambda: farmer_rule(f, ctx))
            f["decision"] = trace
        await asyncio.gather(*(farmer_one(fid) for fid, f in state["farmers"].items() if f["status"] == "uncommitted"))
        return calls.clean({'state': state, 'contexts': contexts, 'traces': calls.traces,
                            'disabled': calls.disabled, 'previous_environment': current['environment']})
    finally:
        if owns_client:
            await client.aclose()


async def finish_tick(prepared, progress, key='', client=None, *, player_id=None, action=None, reason=''):
    state = copy.deepcopy(prepared['state'])
    tick, env = state['tick'], state['environment']
    contexts = prepared['contexts']
    owns_client = state['mode'] == 'online' and client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), limits=httpx.Limits(max_connections=4))
    calls = ModelCalls(state['mode'], key, progress, client)
    calls.traces = copy.deepcopy(prepared['traces'])
    calls.disabled = prepared['disabled']
    try:
        progress["phase"] = "结算与指标汇总"
        if player_id and state['farmers'][player_id]['status'] == 'uncommitted':
            f = state['farmers'][player_id]
            f['jev_reference'] = copy.deepcopy(f['decision'])
            for trace in calls.traces:
                if trace['entity_id'] == player_id:
                    trace['purpose'] = 'human_comparison'
            f['decision'] = {'id': f'{tick}:{player_id}:human', 'tick': tick, 'entity_id': player_id,
                'role': 'farmer', 'source': 'human', 'model': None,
                'input': f['jev_reference']['input'], 'result': {'adopt': action == 'adopt', 'reason': reason}}
        for fid, f in list(state["farmers"].items()):
            p = f["decision"]["result"].get("p_adopt") if f["status"] == "uncommitted" else None
            settled = settle_farmer(f, contexts[fid], tick, p, adopt_override=(action == 'adopt') if fid == player_id and f['status'] == 'uncommitted' else None)
            state["farmers"][fid] = settled
            e = settled["economics"]
            bank = state["banks"][f["bank_id"]]
            bank["cash_cny"] = round(bank["cash_cny"] - e["draw_cny"] + e["principal_paid_cny"] + e["interest_paid_cny"], 2)
            bank["disbursed_cny"] = round(bank["disbursed_cny"] + e["draw_cny"], 2)
            bank["principal_received_cny"] = round(bank["principal_received_cny"] + e["principal_paid_cny"], 2)
            bank["interest_received_cny"] = round(bank["interest_received_cny"] + e["interest_paid_cny"], 2)
            state["bureaus"][f["bureau_id"]]["subsidy_spent_cny"] += e["subsidy_paid_cny"]
            for approval in bank["approvals"]:
                if approval["farmer_id"] == fid:
                    approval["used"] = e["draw_cny"] > 0
        for f in state["farmers"].values():
            f["history"] = (f["history"] + [{"tick": tick, "cash_cny": f["cash_cny"], "status": f["status"],
                "neighbors_operating": sum(state["farmers"][n]["status"] == "operating" for n in f["neighbors"]),
                "water_price": state["bureaus"][f["bureau_id"]]["water_price"],
                "subsidy_rate": state["bureaus"][f["bureau_id"]]["subsidy_rate"], "drought": env["drought"]}])[-2:]
        aggregate(state)
        shared_metrics = copy.deepcopy(state['metrics'])
        progress['phase'] = '地方局汇总反馈与制定下季政策'
        all_area = sum(f['area_mu'] for f in state['farmers'].values())
        async def bureau_one(bid):
            b = state["bureaus"][bid]
            farmers = [f for f in state["farmers"].values() if f["bureau_id"] == bid]
            area = sum(f["area_mu"] for f in farmers)
            quota = all_area * WATER_PER_MU * env["redline_ratio"] * area / all_area
            forecast = sum(water(f, env["drought"]) for f in farmers)
            uncommitted = sum(f["equipment_cny"] for f in farmers if f["status"] == "uncommitted")
            # Reserve at most half a cent per payment for independent cent rounding.
            count = sum(f["status"] == "uncommitted" for f in farmers)
            cap = min(.8, max(0, b["budget_cny"] - .005 * count) / uncommitted) if uncommitted else .8
            facts = {"bureau_id": bid, "season": tick, "policy_effective_tick": tick + 1, "environment": env, "area_mu": area,
                "previous_water_price": b["water_price"], "previous_subsidy_rate": b["subsidy_rate"],
                "budget_cny": b["budget_cny"], "subsidy_cap": cap,
                "water_price_floor": env['water_price_floor'], "subsidy_floor": min(cap, env['subsidy_floor']),
                "base_quota_m3": quota, "forecast_water_m3": forecast,
                "offer_cap_m3": min(max(0, quota - forecast), .2 * quota),
                "previous_regions": [{"id": k, "water_price": v["water_price"], "subsidy_rate": v["subsidy_rate"],
                                      "rank": v["rank"], "metrics": shared_metrics[k]} for k, v in state["bureaus"].items()]}
            facts['household_feedback'] = {
                'metrics': shared_metrics[bid],
                'new_commitments': sum(f['commitment_tick'] == tick for f in farmers),
                'loan_applications': sum(len(bank['approvals']) for bank in state['banks'].values() if bank['bureau_id'] == bid),
                'loan_rejections': sum(not a['approve'] for bank in state['banks'].values() if bank['bureau_id'] == bid for a in bank['approvals']),
                'subsidy_spent_cny': b['subsidy_spent_cny'], 'relief_spent_cny': b['relief_spent_cny']}
            facts['affordability'] = affordability(farmers, b, env, prepared['previous_environment'])
            facts['water_price_ceiling'] = facts['affordability']['water_price_ceiling']
            trace = await calls.decide("bureau", bid, facts, lambda: bureau_rule(facts))
            b['decision'] = trace
            b['next_policy'] = {'effective_tick': tick + 1, 'water_price': trace['result']['water_price'],
                                'subsidy_rate': trace['result']['subsidy_rate']}
            return bid, trace["result"]
        decisions = dict(await asyncio.gather(*(bureau_one(bid) for bid in state['bureaus'])))
        state['quota_transfers'] = match_quotas(state['bureaus'], decisions)
        aggregate(state)
        state['schedule'] = ['农户核算与融资申请', '农商行授信审批', 'Jev农户采纳决策', '结算与指标汇总', '地方局汇总反馈与制定下季政策']
        state["history"].append({"tick": tick, "scenario": env["scenario"], "changed": env != prepared['previous_environment'],
                                  "metrics": copy.deepcopy(state["metrics"])})
        state["traces"] = sorted(calls.traces, key=lambda r: r["id"])
        state["call_summary"] = {"total": len(calls.traces), "online": sum(t["source"] != "rule" for t in calls.traces),
                                 "rule": sum(t["source"] == "rule" for t in calls.traces), "notice": calls.disabled,
                                 "online_unavailable": bool(calls.disabled)}
        if player_id:
            f = state['farmers'][player_id]
            state.setdefault('player_turns', []).append({'tick': tick, 'action': action, 'reason': reason,
                'status': f['status'], 'cash_cny': f['cash_cny'],
                'water_m3': f['water_m3'], 'saved_m3': round(f['area_mu'] * WATER_PER_MU * (1-env['drought']) - f['water_m3'], 4),
                'economics': copy.deepcopy(f['economics']), 'loan': copy.deepcopy(f['loan']),
                'comparison': copy.deepcopy(f.get('jev_reference')) if action != 'continue' else None})
        return calls.clean(state)
    finally:
        if owns_client:
            await client.aclose()


async def execute_tick(current, request, progress, key='', client=None):
    prepared = await prepare_tick(current, request, progress, key, client)
    return await finish_tick(prepared, progress, key, client)

import asyncio
import copy
import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape
from shapely.ops import unary_union

from backend.main import create_app
from backend.sim.agent_models import Environment, Setup, TickRequest
from backend.sim.config import JEV_MODEL, PRESETS
from backend.sim.economics import aggregate, context, settle_farmer
from backend.sim.fallback_rules import farmer_rule
from backend.sim.population import create_state
from backend.sim.tick_engine import execute_tick, match_quotas
from backend.utils.logger import read_run, save_snapshot


def tick(state, environment=None, **kwargs):
    req = TickRequest(run_id=state["run_id"], expected_tick=state["tick"],
                      environment=environment or Environment(), mode=kwargs.pop("mode", "offline"))
    return asyncio.run(execute_tick(state, req, {}, **kwargs))


@pytest.fixture
def initial():
    return create_state(Setup(farmers_per_village=12))


def test_random_households_and_distinct_village_boundaries():
    setup = Setup(farmers_min=12, farmers_max=18)
    state = create_state(setup)
    repeat = create_state(setup)
    counts = [len(v['farmers']) for v in state['villages'].values()]
    assert all(12 <= n <= 18 for n in counts)
    assert len(set(counts)) > 1
    assert state['villages'] == repeat['villages']
    outlines = set()
    for village in state['villages'].values():
        boundary = shape(village['boundary'])
        # Normalize away area so this checks actual outline variation.
        outlines.add(tuple(round(x / math.sqrt(boundary.area), 4) for point in boundary.exterior.coords for x in point))
        assert boundary.area == pytest.approx(len(village['farmers']) * 30 * 2000 / 3)
    assert len(outlines) == len(state['villages'])
    for farmer in state['farmers'].values():
        assert 100 <= farmer['normal_surplus_per_mu'] <= 150
        assert farmer['normal_surplus_cny'] == pytest.approx(farmer['area_mu'] * farmer['normal_surplus_per_mu'], abs=.02)


def test_affordability_cuts_price_and_event_floor_takes_precedence(initial):
    from backend.sim.economics import affordability
    from backend.sim.fallback_rules import bureau_rule
    from backend.sim.llm_client import validate
    b=initial['bureaus']['B01']
    b['water_price']=.7
    farmers=[f for f in initial['farmers'].values() if f['bureau_id']==b['id']]
    for f in farmers:
        f['normal_surplus_cny']=100
    env=Environment().model_dump()
    feedback=affordability(farmers,b,env,env)
    assert feedback['financial_pressure']
    assert feedback['water_price_ceiling']==pytest.approx(.595)
    facts={'forecast_water_m3':100,'base_quota_m3':90,'previous_water_price':.7,'previous_subsidy_rate':.1,
           'subsidy_cap':.3,'offer_cap_m3':0,'affordability':feedback,'water_price_ceiling':feedback['water_price_ceiling']}
    result=bureau_rule(facts)
    assert result['water_price']==pytest.approx(.595)
    with pytest.raises(ValueError,match='承受能力'):
        validate('bureau',{**result,'water_price':.7},facts)
    floor_env={**env,'water_price_floor':.65}
    assert affordability(farmers,b,floor_env,env)['water_price_ceiling']==.65
    for f in farmers:
        f['normal_surplus_cny']=100000
    assert affordability(farmers,b,env,{**env,'drought':.4})['drought_easing']
    assert affordability(farmers,b,env,{**env,'drought':.4})['water_price_ceiling']==pytest.approx(.595)


def test_relief_is_budgeted_targeted_and_counted_once(initial):
    from backend.sim.economics import distribute_relief
    state=copy.deepcopy(initial)
    state['environment']['drought']=.4
    for f in state['farmers'].values():
        f.update(cash_cny=0,status='uncommitted',relief_cny=0)
    rich=next(iter(state['farmers'].values()))
    rich['cash_cny']=1000000
    operating=list(state['farmers'].values())[1]
    operating['status']='operating'
    opening={fid:f['cash_cny'] for fid,f in state['farmers'].items()}
    distribute_relief(state)
    assert rich['relief_cny']==0 and operating['relief_cny']==0
    assert sum(f['relief_cny'] for f in state['farmers'].values())>0
    for b in state['bureaus'].values():
        paid=sum(f['relief_cny'] for f in state['farmers'].values() if f['bureau_id']==b['id'])
        assert paid==pytest.approx(b['relief_spent_cny'])
        assert paid<=b['relief_budget_cny']
    for fid,f in state['farmers'].items():
        c=context(f,state['bureaus'][f['bureau_id']],state['environment'],state['farmers'])
        assert f['relief_cny']<=.5*c['drought_loss_cny']+.001
        end=settle_farmer(f,c,1,0)
        assert end['cash_cny']==pytest.approx(round(opening[fid]+f['relief_cny']+c['furrow_surplus_cny']+(c['gain_cny'] if f['status']=='operating' else 0),2))


def test_late_adopters_can_keep_waiting_without_the_previous_cash_trap(monkeypatch):
    # Deliberately keep every new farmer waiting: recovery must not require forced adoption.
    monkeypatch.setattr('backend.sim.tick_engine.farmer_rule',lambda f,c:{'p_adopt':0,'rationale':'持续观望的对照'})
    state=create_state(Setup(bureaus=3,villages_per_bureau=3,farmers_min=12,farmers_max=16,seed=42,events=[
        {'kind':'drought','season':3,'end_season':6},
        {'kind':'subsidy','season':4,'end_season':10},
        {'kind':'redline','season':7,'end_season':10}]))
    for season in range(1,11):
        state=tick(state)
        if season==6:
            drought_price=state['bureaus']['B03']['water_price']
        if season==7:
            assert state['bureaus']['B03']['water_price']<drought_price
    focal=state['farmers']['B03-V03-F04']
    assert focal['status']=='uncommitted'
    assert focal['cash_cny']>0


def test_events_are_applied_by_absolute_season_and_expire():
    state = create_state(Setup(farmers_per_village=12, events=[
        {'kind': 'drought', 'season': 1, 'end_season': 1},
        {'kind': 'campaign', 'season': 1, 'end_season': 2},
        {'kind': 'subsidy', 'season': 2, 'end_season': 2}]))
    first = tick(state)
    assert first['environment']['drought'] == .4
    assert first['environment']['enthusiasm'] == .8
    assert all(b['water_price'] >= .5 for b in first['bureaus'].values())
    assert first['base_environment']['drought'] == 0
    second = tick(first)
    assert second['environment']['drought'] == 0
    assert second['environment']['subsidy_floor'] == .55
    for b in second['bureaus'].values():
        assert b['subsidy_rate'] >= min(.55, b['decision']['input']['subsidy_cap'])
        assert b['subsidy_spent_cny'] <= b['budget_cny'] + .01
    third = tick(second)
    assert third['active_events'] == []
    assert third['environment']['enthusiasm'] == 0
    assert third['environment']['subsidy_floor'] == 0
    assert third['environment']['budget_multiplier'] == 1
    assert first['environment']['drought'] == .4


@pytest.mark.parametrize("setup", [Setup(), Setup(bureaus=1, villages_per_bureau=2, farmers_per_village=12), Setup(bureaus=6, villages_per_bureau=4, farmers_per_village=18)])
def test_population_spatial_area_and_repeatability(setup):
    s = create_state(setup)
    again = create_state(setup)
    assert s["farmers"] == again["farmers"]
    assert len(s["farmers"]) == setup.bureaus * setup.villages_per_bureau * setup.farmers_per_village
    for v in s["villages"].values():
        cells = [shape(p["geometry"]) for p in v["parcels"]]
        assert sum(p.area for p in cells) == pytest.approx(setup.farmers_per_village * 30 * 2000 / 3)
        assert unary_union(cells).symmetric_difference(shape(v["boundary"])).area < 1e-6
        for fid in v["farmers"]:
            f = s["farmers"][fid]
            assert all(fid in s["farmers"][n]["neighbors"] and n in v["farmers"] for n in f["neighbors"])
        assert sum(s["farmers"][fid]["initial_adopter"] for fid in v["farmers"]) == 2
    completed = tick(s)
    assert completed["tick"] == 1
    assert completed["metrics"]["basin"]["farmer_count"] == len(s["farmers"])
    assert completed["call_summary"]["total"] == len(s["bureaus"]) + len(s["banks"]) + len(s["farmers"]) - 2 * len(s["villages"])


def test_cash_first_and_actual_water_costs(initial):
    f = next(f for f in initial["farmers"].values() if f["status"] == "uncommitted")
    f["cash_cny"] = 1_000_000
    b = initial["bureaus"][f["bureau_id"]]
    c0 = context(f, b, initial["environment"], initial["farmers"])
    b["water_price"] = .3
    c1 = context(f, b, initial["environment"], initial["farmers"])
    assert c0["required_loan_cny"] == 0
    assert c0["financing_feasible"]
    assert c1["gain_cny"] - c0["gain_cny"] == pytest.approx(f["area_mu"] * 300 * .3 * .1, abs=.02)
    assert c1["water_extra_cny"] == pytest.approx(f["area_mu"] * 300 * .1, abs=.01)


def test_cash_factor_creates_real_credit_demand_and_loans_are_repaid():
    events = [{'kind': 'campaign', 'season': 2, 'end_season': 10},
              {'kind': 'drought', 'season': 3, 'end_season': 4},
              {'kind': 'subsidy', 'season': 5, 'end_season': 7},
              {'kind': 'credit', 'season': 7, 'end_season': 8},
              {'kind': 'redline', 'season': 9, 'end_season': 10}]
    state = create_state(Setup(farmers_min=12, farmers_max=18, events=events))
    reference = create_state(Setup(farmers_min=12, farmers_max=18, events=events, initial_cash_factor=1))
    assert state['villages'] == reference['villages']
    for fid, f in state['farmers'].items():
        assert f['cash_cny'] == round(reference['farmers'][fid]['cash_cny'] * .65, 2)
        assert f['normal_surplus_cny'] == reference['farmers'][fid]['normal_surplus_cny']
    used, refused = [], []
    for _ in range(10):
        state = tick(state)
        for bank in state['banks'].values():
            for a in bank['approvals']:
                if not a['approve']:
                    refused.append(a)
                if a['used']:
                    used.append(a)
                    assert a['approve'] and a['amount_cny'] == a['requested_cny'] > 0
                    assert a['assessment']['coverage'] + .0001 >= a['assessment']['minimum_coverage']
                    assert state['farmers'][a['farmer_id']]['loan']['original_cny'] == a['amount_cny']
            assert bank['cash_cny'] >= 0
            assert bank['cash_cny'] == pytest.approx(bank['initial_cash_cny'] - bank['disbursed_cny'] + bank['principal_received_cny'] + bank['interest_received_cny'], abs=.01)
    assert used and refused
    assert sum(b['interest_received_cny'] for b in state['banks'].values()) > 0
    assert any(f['loan'] and f['loan']['principal_cny'] == 0 for f in state['farmers'].values())


def test_commitment_installation_and_frozen_neighbors(initial):
    original = copy.deepcopy(initial)
    result = tick(initial)
    assert initial == original
    new = [f for f in result["farmers"].values() if f["commitment_tick"] == 1]
    assert new
    for f in new:
        assert f["status"] == "pending"
        assert f["economics"]["operating_gain_cny"] == 0
        c = f["decision"]["input"]["economics"]
        assert c["neighbors_operating"] == sum(initial["farmers"][n]["status"] == "operating" for n in f["neighbors"])
    second = tick(result)
    for f in new:
        assert second["farmers"][f["id"]]["status"] == "operating"
        assert second["farmers"][f["id"]]["decision"]["tick"] == 1
    assert not {f["id"] for f in new} & {t["entity_id"] for t in second["traces"]}


def test_five_installments_interest_and_arrears(initial):
    f = copy.deepcopy(next(f for f in initial["farmers"].values() if f["status"] == "uncommitted"))
    f["cash_cny"] = 0
    ctx = {"investment_cny": 1000, "required_loan_cny": 1000, "subsidy_cny": 100, "rate": .04,
           "financing_feasible": True, "gain_cny": 10000, "furrow_surplus_cny": 0}
    f = settle_farmer(f, ctx, 1, .9)
    assert f["cash_cny"] == 0 and f["status"] == "pending"
    unpaid = copy.deepcopy(f)
    f["status"] = "operating"
    for season in range(2, 7):
        f = settle_farmer(f, ctx, season, None)
    assert f["loan"]["principal_cny"] == 0
    assert f["loan"]["paid_interest_cny"] == 120
    assert f["loan"]["repaid_principal_cny"] == 1000
    assert f["loan"]["overdue_seasons"] == 0
    unpaid["status"] = "operating"
    bad = {**ctx, "gain_cny": 0, "furrow_surplus_cny": -100}
    for season in (2, 3):
        unpaid = settle_farmer(unpaid, bad, season, None)
    assert unpaid["loan"]["overdue_seasons"] == 2
    assert unpaid["loan"]["principal_arrears_cny"] == 400
    assert unpaid["loan"]["interest_arrears_cny"] == 80


def test_declined_and_unused_credit_prevent_draw(initial):
    f = copy.deepcopy(next(f for f in initial["farmers"].values() if f["status"] == "uncommitted"))
    f["cash_cny"] = 0
    b = initial["bureaus"][f["bureau_id"]]
    f["financing"] = {"approved": False, "rate": .04}
    c = context(f, b, initial["environment"], initial["farmers"])
    denied = settle_farmer(f, c, 1, .99)
    assert denied["status"] == "uncommitted" and denied["loan"] is None
    f["financing"]["approved"] = True
    c = context(f, b, initial["environment"], initial["farmers"])
    declined = settle_farmer(f, c, 1, .01)
    assert declined["loan"] is None and declined["economics"]["draw_cny"] == 0


def test_quota_matching_is_proportional_and_conserved():
    bureaus = {b: {} for b in "abcd"}
    d = {"a": {"quota_delta_m3": -100}, "b": {"quota_delta_m3": -200},
         "c": {"quota_delta_m3": 100}, "d": {"quota_delta_m3": 500}}
    transfers = match_quotas(bureaus, d)
    assert sum(t["volume_m3"] for t in transfers) == pytest.approx(300)
    assert bureaus["c"]["quota_delta_m3"] == pytest.approx(50)
    assert bureaus["d"]["quota_delta_m3"] == pytest.approx(250)
    assert sum(b["quota_delta_m3"] for b in bureaus.values()) == pytest.approx(0)


def test_budgets_balances_water_and_offline_reproducibility(initial):
    def run():
        s = copy.deepcopy(initial)
        for i in range(8):
            env = Environment(**{k: v for k, v in PRESETS["drought" if i >= 3 else "baseline"].items() if k != "name"})
            s = tick(s, env)
            assert all(t["source"] == "rule" for t in s["traces"])
            for b in s["bureaus"].values():
                assert b["subsidy_spent_cny"] <= b["budget_cny"] + .02
            for b in s["banks"].values():
                assert b["cash_cny"] >= 0
                assert b["cash_cny"] == pytest.approx(b["initial_cash_cny"] - b["disbursed_cny"] + b["principal_received_cny"] + b["interest_received_cny"], abs=.01)
                assert sum(a["amount_cny"] for a in b["approvals"]) <= b["decision"]["input"]["available_cny"] + .01
            m = s["metrics"]["basin"]
            assert m["water_m3"] == pytest.approx(sum(s["metrics"][v]["water_m3"] for v in s["villages"]))
            assert m["redline_m3"] == pytest.approx(sum(b["quota_m3"] for b in s["bureaus"].values()))
        return s
    assert run() == run()


def test_interest_reduces_rule_adoption(initial):
    f = next(f for f in initial["farmers"].values() if f["status"] == "uncommitted")
    f["cash_cny"] = 0
    b = initial["bureaus"][f["bureau_id"]]
    f["financing"] = {"approved": True, "rate": .01}
    low = context(f, b, initial["environment"], initial["farmers"])
    f["financing"]["rate"] = .12
    high = context(f, b, initial["environment"], initial["farmers"])
    assert low["five_season_net_cny"] > high["five_season_net_cny"]
    assert farmer_rule(f, low)["p_adopt"] >= farmer_rule(f, high)["p_adopt"]


def test_cautious_decision_estimate_does_not_reduce_actual_cash_gain(initial):
    from backend.sim.jev_client import farmer_facts
    f = copy.deepcopy(next(f for f in initial["farmers"].values() if f["status"] == "uncommitted"))
    f["cash_cny"] = 1_000_000
    b = initial["bureaus"][f["bureau_id"]]
    ctx = context(f, b, initial["environment"], initial["farmers"])
    assert ctx["cautious_expected_gain_cny"] == pytest.approx(.9 * ctx["gain_cny"], abs=.02)
    assert ctx["cautious_five_season_net_cny"] < ctx["five_season_net_cny"]
    assert ctx["preferred_cash_buffer_cny"] == 2 * f["normal_surplus_cny"]
    central = {**ctx, "cautious_five_season_net_cny": ctx["five_season_net_cny"]}
    assert farmer_rule(f, ctx)["p_adopt"] <= farmer_rule(f, central)["p_adopt"]
    f.update(status="operating", commitment_tick=0, installation_tick=0)
    result = settle_farmer(f, ctx, 1, None)
    assert result["economics"]["operating_gain_cny"] == ctx["gain_cny"]
    facts = farmer_facts(f, ctx, 1)
    assert "No investment commitment has been made" in facts["decision_to_evaluate"]
    assert any("Credit approval only" in p for p in facts["decision_preferences"])


def test_online_payloads_raw_results_and_secret_redaction(initial):
    key = "test-secret-never-save"
    received = []
    def handler(request):
        body = json.loads(request.content)
        received.append(body)
        assert key not in json.dumps(body)
        assert request.headers["authorization"] == "Bearer " + key
        if "questions" in body:
            return httpx.Response(200, json={"model": JEV_MODEL + "-20261014", "answers": {"adopt_drip": {"type": "noul", "noul": .8}}})
        facts = json.loads(body["messages"][1]["content"])
        if "bank_id" in facts:
            assert all({"age", "skill", "risk_adjusted_surplus_cny", "minimum_coverage"} <= a.keys() for a in facts["applicants"])
        from backend.sim.fallback_rules import bank_rule, bureau_rule
        value = bureau_rule(facts) if "bureau_id" in facts else bank_rule(facts)
        value['type'] = 'json_object'
        return httpx.Response(200, json={"model": "deepseek/deepseek-v4.1-flash", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            req = TickRequest(run_id=initial["run_id"], expected_tick=0, environment=Environment(), mode="online")
            return await execute_tick(initial, req, {}, key, client)
    s = asyncio.run(run())
    assert s["call_summary"]["rule"] == 0
    assert s["call_summary"]["online"] == len(received)
    assert key not in json.dumps(s)
    assert all(t["attempts"][0]["raw"] == t["raw"] for t in s["traces"])
    roles=[]
    for body in received:
        if 'questions' in body:
            roles.append('farmer')
        else:
            facts=json.loads(body['messages'][1]['content'])
            roles.append('bank' if 'bank_id' in facts else 'bureau')
            if 'bureau_id' in facts:
                assert facts['policy_effective_tick']==2
                assert facts['household_feedback']['metrics']['farmer_count']==s['metrics'][facts['bureau_id']]['farmer_count']
                assert facts['household_feedback']['new_commitments']==sum(f['commitment_tick']==1 for f in s['farmers'].values() if f['bureau_id']==facts['bureau_id'])
    assert roles==sorted(roles,key={'bank':0,'farmer':1,'bureau':2}.get)
    for trace in s['traces']:
        if trace['role'] in ('bank', 'bureau'):
            assert 'type' not in trace['result']
            assert json.loads(trace['raw']['choices'][0]['message']['content'])['type'] == 'json_object'


def test_bureau_policy_is_announced_after_settlement_and_applied_next_season(initial,monkeypatch):
    from backend.sim.fallback_rules import bureau_rule
    def policy(facts):
        return {**bureau_rule(facts),'water_price':.23,'subsidy_rate':.2}
    monkeypatch.setattr('backend.sim.tick_engine.bureau_rule',policy)
    monkeypatch.setattr('backend.sim.tick_engine.farmer_rule',lambda f,c:{'p_adopt':0})
    for f in initial['farmers'].values():
        f.update(cash_cny=1000000,normal_surplus_cny=10000)
    first=tick(initial)
    for b in first['bureaus'].values():
        assert b['water_price']==.2 and b['subsidy_rate']==.1
        assert b['next_policy']=={'effective_tick':2,'water_price':.23,'subsidy_rate':.2}
    for f in first['farmers'].values():
        assert f['economics']['water_price']==.2
        assert f['economics']['subsidy_rate']==.1
        if f['status']=='uncommitted':
            assert f['loan_request']['tick']==1
    second=tick(first)
    assert all(b['water_price']==.23 and b['subsidy_rate']==.2 for b in second['bureaus'].values())
    assert all(f['economics']['water_price']==.23 for f in second['farmers'].values())


def test_credit_assesses_age_and_operation_ability_without_age_cutoff(initial):
    from backend.sim.economics import credit_profile
    from backend.sim.fallback_rules import bank_rule
    from backend.sim.llm_client import validate
    f = copy.deepcopy(next(f for f in initial["farmers"].values() if f["status"] == "uncommitted"))
    f.update(cash_cny=0, normal_surplus_cny=1000)
    c = context(f, initial["bureaus"][f["bureau_id"]], initial["environment"], initial["farmers"])
    c.update(required_loan_cny=10000, cautious_expected_gain_cny=2700, furrow_surplus_cny=1000, operating_surplus_cny=4000, gain_cny=3000)
    candidates = [credit_profile({**f, "id": name, "age": age, "skill": skill}, c)
                  for name, age, skill in [("young-skilled", 32, 4), ("older-skilled", 61, 4), ("older-low-skill", 61, 1)]]
    young, older, low = candidates
    assert young["risk_adjusted_surplus_cny"] == older["risk_adjusted_surplus_cny"]
    assert older["minimum_coverage"] > young["minimum_coverage"]
    assert low["risk_adjusted_surplus_cny"] < older["risk_adjusted_surplus_cny"]
    assert low["minimum_coverage"] > older["minimum_coverage"]
    facts = {"credit_factor": 1., "overdue_ratio": 0., "available_cny": 50000, "applicants": candidates}
    result = bank_rule(facts)
    decisions = {a["farmer_id"]: a for a in result["approvals"]}
    assert decisions["young-skilled"]["approve"]
    assert decisions["older-skilled"]["approve"]
    assert not decisions["older-low-skill"]["approve"]
    validate("bank", result, facts)
    wrong = copy.deepcopy(result)
    wrong['type'] = 'json_object'
    for a in wrong["approvals"]:
        a["approve"] = True
    with pytest.raises(ValueError, match="风险调整后覆盖率"):
        validate("bank", wrong, facts)


@pytest.mark.parametrize("failure", ["auth", "json", "network", "invalid_probability"])
def test_online_failures_are_labelled_and_bounded(initial, failure):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if failure == "auth":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        if failure == "network":
            raise httpx.ConnectError("network unavailable")
        if failure == "invalid_probability" and "questions" in json.loads(request.content):
            return httpx.Response(200, json={"model": JEV_MODEL, "answers": {"adopt_drip": {"type": "noul", "noul": 2}}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            req = TickRequest(run_id=initial["run_id"], expected_tick=0, environment=Environment(), mode="online")
            return await execute_tick(initial, req, {}, "dummy", client)
    s = asyncio.run(run())
    assert s["tick"] == 1 and s["call_summary"]["rule"] == len(s["traces"])
    assert any(t["error"] for t in s["traces"])
    assert all(len(t["attempts"]) <= 2 for t in s["traces"])
    if failure in ("auth", "network"):
        assert count <= 8
        assert s["call_summary"]["online_unavailable"]
    else:
        assert not s["call_summary"]["online_unavailable"]


@pytest.mark.parametrize('failure',['response_disconnect','read_timeout','validation_then_disconnect','validation_then_connect'])
def test_one_agent_response_failure_does_not_disable_other_online_agents(initial,failure):
    from backend.sim.fallback_rules import bank_rule,bureau_rule
    failed_bank=next(iter(initial['banks']))
    attempts=0
    def handler(request):
        nonlocal attempts
        body=json.loads(request.content)
        if 'questions' in body:
            return httpx.Response(200,json={'model':JEV_MODEL,'answers':{'adopt_drip':{'type':'noul','noul':.3}}})
        facts=json.loads(body['messages'][1]['content'].split('\n上次输出无效')[0])
        if facts.get('bank_id')==failed_bank:
            attempts+=1
            if failure.startswith('validation_then') and attempts==1:
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"rate":0.04}'}}]})
            if failure=='read_timeout':
                raise httpx.ReadTimeout('slow response',request=request)
            if failure=='validation_then_connect':
                raise httpx.ConnectError('connection closed',request=request)
            raise httpx.RemoteProtocolError('peer closed connection without sending complete message body',request=request)
        value=bank_rule(facts) if 'bank_id' in facts else bureau_rule(facts)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request=TickRequest(run_id=initial['run_id'],expected_tick=0,mode='online',environment=Environment())
            return await execute_tick(initial,request,{},'test-key',client)
    result=asyncio.run(run())
    assert attempts==2
    assert result['call_summary']['rule']==1
    assert not result['call_summary']['online_unavailable']
    assert any(t['source']=='jev' for t in result['traces'])
    error=next(t for t in result['traces'] if t['entity_id']==failed_bank)
    assert error['attempts'][-1]['error_type']
    assert error['attempts'][-1]['error_detail']


def test_api_history_conflicts_and_replay(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        s = client.get("/api/state").json()
        assert s["tick"] == 0
        payload = {"run_id": s["run_id"], "expected_tick": 0, "environment": Environment().model_dump()}
        result = client.post("/api/tick", json=payload)
        assert result.status_code == 200
        assert result.json()["tick"] == 1
        assert client.post("/api/tick", json=payload).status_code == 409
        saved = client.get("/api/runs/" + s["run_id"]).json()
        assert [s["tick"] for s in saved["snapshots"]] == [0, 1]
        assert saved["snapshots"][1] == result.json()
        assert client.get("/api/runs/not-found").status_code == 404
        assert client.post("/api/tick", json={**payload, "expected_tick": 1, "mode": "online"}).status_code == 400
        assert client.post("/api/reset", json={"bureaus": 7}).status_code == 422
        reset = client.post("/api/reset", json={"bureaus": 6, "villages_per_bureau": 4, "initial_cash_factor": .65})
        assert reset.status_code == 200
        assert reset.json()['setup']['initial_cash_factor'] == .65
        assert reset.json()['villages']['B06-V04']['name'] == '润泽四村'


def test_already_installed_and_no_applicants(initial):
    for f in initial["farmers"].values():
        f.update(status="operating", commitment_tick=0, installation_tick=0)
    aggregate(initial)
    s = tick(initial)
    assert all(t["role"] != "farmer" for t in s["traces"])
    assert all(not b["approvals"] for b in s["banks"].values())
    assert s["metrics"]["basin"]["installed_rate"] == 1


def test_snapshot_can_be_read_independently(initial, tmp_path):
    save_snapshot(initial, tmp_path)
    assert read_run(initial["run_id"], tmp_path)["snapshots"][0] == json.loads(json.dumps(initial))

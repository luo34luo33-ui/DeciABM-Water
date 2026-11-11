"""Current-conditions economics adapted from Level3; all values are CNY per season."""
import copy
import math

from .config import BASE_PRICE, FARMER_BUFFER_SEASONS, FARMER_EXPECTED_GAIN_FACTOR, RELIEF_BUDGET_PER_MU, RELIEF_LOSS_SHARE, SAVING, WATER_PER_MU


def water(farmer, drought, operating=None):
    if operating is None:
        operating = farmer["status"] == "operating"
    return farmer["area_mu"] * WATER_PER_MU * (1 - drought) * (1 - SAVING if operating else 1)


def context(f, bureau, env, farmers, *, previous=None):
    normal_water = f["area_mu"] * WATER_PER_MU
    furrow = normal_water * (1 - env["drought"])
    drought_loss = 2500 * f["area_mu"] / 30 * env["drought"] / .3
    water_extra = furrow * bureau["water_price"] - normal_water * BASE_PRICE
    # base_gain already includes baseline water savings. Add only the change.
    gain = f["base_gain_cny"] + drought_loss + SAVING * (furrow * bureau["water_price"] - normal_water * BASE_PRICE)
    subsidy = round(f["equipment_cny"] * bureau["subsidy_rate"], 2)
    investment = round(f["equipment_cny"] - subsidy, 2)
    required = round(max(0, investment - max(0, f["cash_cny"])), 2)
    snapshot = previous if previous is not None else farmers
    installed = sum(snapshot[n]["status"] == "operating" for n in f["neighbors"])
    fin = f.get("financing", {})
    rate = fin.get("rate", .03)
    feasible = f["cash_cny"] >= 0 and (required == 0 or fin.get("approved", False))
    expected_gain = round(gain * FARMER_EXPECTED_GAIN_FACTOR, 2)
    first_payment = required * (.2 + rate)
    return {"area_mu": f["area_mu"], "cash_cny": f["cash_cny"], "equipment_cny": f["equipment_cny"],
            "opening_cash_cny": round(f['cash_cny'] - f.get('relief_cny', 0), 2),
            "relief_cny": f.get('relief_cny', 0), "normal_surplus_cny": f['normal_surplus_cny'],
            "subsidy_cny": subsidy, "investment_cny": investment, "required_loan_cny": required,
            "gain_cny": round(gain, 2), "drought_loss_cny": round(drought_loss, 2),
            "water_extra_cny": round(water_extra, 2),
            "furrow_surplus_cny": round(f["normal_surplus_cny"] - drought_loss - water_extra, 2),
            "operating_surplus_cny": round(f["normal_surplus_cny"] - drought_loss - water_extra + gain, 2),
            "neighbors_operating": installed, "neighbor_count": len(f["neighbors"]), "rate": rate,
            "five_season_interest_cny": round(3 * required * rate, 2),
            "five_season_net_cny": round(5 * gain - investment - 3 * required * rate, 2),
            "cautious_expected_gain_cny": expected_gain,
            "cautious_five_season_net_cny": round(5 * expected_gain - investment - 3 * required * rate, 2),
            "cash_after_investment_cny": round(f["cash_cny"] + required - investment, 2),
            "commitment_season_cash_cny": round(f["cash_cny"] + required - investment + f["normal_surplus_cny"] - drought_loss - water_extra, 2),
            "first_repayment_cny": round(first_payment, 2),
            "cautious_operating_cash_after_debt_cny": round(f["normal_surplus_cny"] - drought_loss - water_extra + expected_gain - first_payment, 2),
            "preferred_cash_buffer_cny": round(max(0, f["normal_surplus_cny"]) * FARMER_BUFFER_SEASONS, 2),
            "financing_feasible": feasible, "water_price": bureau["water_price"],
            "subsidy_rate": bureau["subsidy_rate"], "drought": env["drought"],
            "enthusiasm": env.get("enthusiasm", 0)}


def affordability(farmers, bureau, env, previous_env):
    """Shared pre-policy snapshot; no access to other bureaus' current decisions."""
    snapshot = {f['id']: f for f in farmers}
    forecasts = [context(f, bureau, env, snapshot) for f in farmers]
    losses = sum((c['operating_surplus_cny'] if f['status'] == 'operating' else c['furrow_surplus_cny']) < 0 for f, c in zip(farmers, forecasts))
    loss_share = losses / len(farmers)
    cash_share = sum(f['cash_cny'] < 0 for f in farmers) / len(farmers)
    drought_ended = previous_env['drought'] > env['drought'] and previous_env['drought'] > 0
    stressed = loss_share >= .25 or cash_share >= .10
    # Event floors take precedence; hardship or easing drought removes the price ratchet.
    ceiling = min(1., max(env.get('water_price_floor', .1), round(bureau['water_price'] * (.85 if stressed or drought_ended else 1.15), 4)))
    return {'forecast_loss_household_rate': loss_share, 'negative_cash_household_rate': cash_share,
            'mean_normal_surplus_cny': round(sum(f['normal_surplus_cny'] for f in farmers) / len(farmers), 2),
            'previous_mean_surplus_cny': round(sum(f.get('economics', {}).get('furrow_surplus_cny', f['normal_surplus_cny']) + f.get('economics', {}).get('operating_gain_cny', 0) for f in farmers) / len(farmers), 2),
            'drought_easing': drought_ended, 'financial_pressure': stressed, 'water_price_ceiling': ceiling}


def distribute_relief(state):
    """Limited disaster grants before credit decisions, independent of equipment aid."""
    env = state['environment']
    for bid, bureau in state['bureaus'].items():
        farmers = sorted((f for f in state['farmers'].values() if f['bureau_id'] == bid), key=lambda f: f['id'])
        budget = round(sum(f['area_mu'] for f in farmers) * RELIEF_BUDGET_PER_MU, 2) if env['drought'] > 0 else 0
        bureau.update(relief_budget_cny=budget, relief_spent_cny=0.)
        claims = []
        for f in farmers:
            if f['status'] == 'operating' or env['drought'] <= 0:
                continue
            c = context(f, bureau, env, state['farmers'])
            projected = f['cash_cny'] + c['furrow_surplus_cny']
            need = max(0, c['preferred_cash_buffer_cny'] - projected)
            claim = min(need, RELIEF_LOSS_SHARE * c['drought_loss_cny'])
            if claim > 0:
                claims.append((f, claim))
        total = sum(amount for _, amount in claims)
        factor = min(1, budget / total) if total else 0
        for f, claim in claims:
            # Floor to cents so proportional allocations cannot exceed the budget.
            grant = math.floor(claim * factor * 100 + 1e-8) / 100
            f['relief_cny'] = grant
            f['relief_received_cny'] = round(f.get('relief_received_cny', 0) + grant, 2)
            f['cash_cny'] = round(f['cash_cny'] + grant, 2)
            bureau['relief_spent_cny'] = round(bureau['relief_spent_cny'] + grant, 2)


def credit_profile(f, ctx):
    """Toy credit assessment: operation risk and a modest continuity margin.

    Age is an initial profile attribute, not a retirement cutoff. These
    assumptions affect credit assessment only, never realized cash settlement.
    """
    ability_factor = .6 + .1 * f["skill"]
    continuity_margin = .15 * max(0, min(1, (f["age"] - 50) / 20))
    minimum_coverage = 1.2 + .3 * (1 - f["skill"] / 4) + continuity_margin
    adjusted_gain = round(ctx["cautious_expected_gain_cny"] * ability_factor, 2)
    loan = f["loan"]
    return {"farmer_id": f["id"], "age": f["age"], "skill": f["skill"],
            "skill_scale": "0=低，1=较低，2=中等，3=较高，4=高", "technical_support": f["support"],
            "required_loan_cny": ctx["required_loan_cny"], "cash_cny": f["cash_cny"],
            "operating_surplus_cny": ctx["operating_surplus_cny"],
            "risk_adjusted_gain_cny": adjusted_gain,
            "risk_adjusted_surplus_cny": round(ctx["furrow_surplus_cny"] + adjusted_gain, 2),
            "ability_gain_factor": ability_factor, "minimum_coverage": round(minimum_coverage, 4),
            "age_continuity_margin": round(continuity_margin, 4),
            "area_mu": f["area_mu"], "five_season_gain_cny": 5 * ctx["gain_cny"],
            "has_arrears": bool(loan and loan["principal_arrears_cny"] + loan["interest_arrears_cny"] > 0)}


def settle_farmer(farmer, ctx, tick, p, *, adopt_override=None):
    from .agent_models import Loan
    f = copy.deepcopy(farmer)
    investment = draw = subsidy = 0.
    wants_adoption = adopt_override if adopt_override is not None else p is not None and p >= .5
    if f["status"] == "uncommitted" and wants_adoption and ctx["financing_feasible"]:
        investment, draw, subsidy = ctx["investment_cny"], ctx["required_loan_cny"], ctx["subsidy_cny"]
        f.update(status="pending", commitment_tick=tick, installation_tick=tick + 1,
                 investment_cny=investment, subsidy_received_cny=subsidy)
        if draw:
            f["loan"] = Loan(original_cny=draw, principal_cny=draw, rate=ctx["rate"], start_tick=tick + 1).model_dump()
    operating_gain = ctx["gain_cny"] if f["status"] == "operating" else 0
    cash = round(f["cash_cny"] + draw - investment + ctx["furrow_surplus_cny"] + operating_gain, 2)
    principal_paid = interest_paid = principal_due = interest_due = 0.
    loan = f["loan"]
    if loan and tick >= loan["start_tick"] and (loan["principal_cny"] > 0 or loan["interest_arrears_cny"] > 0):
        age = tick - loan["start_tick"]
        regular = round(loan["original_cny"] / 5, 2)
        scheduled = regular if age < 4 else round(loan["original_cny"] - 4 * regular, 2) if age == 4 else 0
        principal_due = round(min(loan["principal_cny"], loan["principal_arrears_cny"] + scheduled), 2)
        interest_due = round(loan["interest_arrears_cny"] + loan["principal_cny"] * loan["rate"], 2)
        interest_paid = round(min(max(0, cash), interest_due), 2)
        cash = round(cash - interest_paid, 2)
        principal_paid = round(min(max(0, cash), principal_due), 2)
        cash = round(cash - principal_paid, 2)
        loan["principal_cny"] = round(loan["principal_cny"] - principal_paid, 2)
        loan["principal_arrears_cny"] = round(principal_due - principal_paid, 2)
        loan["interest_arrears_cny"] = round(interest_due - interest_paid, 2)
        loan["repaid_principal_cny"] = round(loan["repaid_principal_cny"] + principal_paid, 2)
        loan["paid_interest_cny"] = round(loan["paid_interest_cny"] + interest_paid, 2)
        loan["overdue_seasons"] = loan["overdue_seasons"] + 1 if principal_due + interest_due - principal_paid - interest_paid > .005 else 0
    f["cash_cny"] = cash
    f["economics"] = {**ctx, "operating_gain_cny": operating_gain, "principal_due_cny": principal_due,
                      "interest_due_cny": interest_due, "principal_paid_cny": principal_paid,
                      "interest_paid_cny": interest_paid, "investment_paid_cny": investment, "draw_cny": draw,
                      "subsidy_paid_cny": subsidy}
    return f


def aggregate(state):
    drought = state["environment"]["drought"]
    all_farmers = list(state["farmers"].values())
    total_area = sum(f["area_mu"] for f in all_farmers)
    redline = total_area * WATER_PER_MU * state["environment"]["redline_ratio"]
    for f in all_farmers:
        f["water_m3"] = round(water(f, drought), 4)
    for b in state["banks"].values():
        loans = [f["loan"] for f in all_farmers if f["bank_id"] == b["id"] and f["loan"]]
        b["outstanding_cny"] = round(sum(l["principal_cny"] for l in loans), 2)
        b["overdue_cny"] = round(sum(l["principal_arrears_cny"] + l["interest_arrears_cny"] for l in loans), 2)
        b["nonperforming_cny"] = round(sum(l["principal_cny"] for l in loans if l["overdue_seasons"] >= 2), 2)
    metrics = {}
    for entity, farmers in [("basin", all_farmers)] + [(bid, [f for f in all_farmers if f["bureau_id"] == bid]) for bid in state["bureaus"]] + [(vid, [state["farmers"][fid] for fid in v["farmers"]]) for vid, v in state["villages"].items()]:
        area = sum(f["area_mu"] for f in farmers)
        installed_area = sum(f["area_mu"] for f in farmers if f["status"] == "operating")
        committed = sum(f["status"] != "uncommitted" for f in farmers)
        banks = [b for b in state["banks"].values() if any(f["bank_id"] == b["id"] for f in farmers)]
        bureau_ids = {f["bureau_id"] for f in farmers}
        bureaus = [state["bureaus"][bid] for bid in bureau_ids]
        if entity in state["bureaus"]:
            bureau = state["bureaus"][entity]
            bureau["base_quota_m3"] = redline * area / total_area
            bureau["quota_m3"] = bureau["base_quota_m3"] + bureau["quota_delta_m3"]
            quota = bureau["quota_m3"]
        elif entity in state["villages"]:
            bid = state["villages"][entity]["bureau_id"]
            bureau_area = sum(f["area_mu"] for f in all_farmers if f["bureau_id"] == bid)
            quota = state["bureaus"][bid]["quota_m3"] * area / bureau_area
        else:
            quota = redline
        actual = sum(f["water_m3"] for f in farmers)
        cf = area * WATER_PER_MU * (1 - drought)
        metrics[entity] = {"water_m3": actual, "redline_m3": quota, "counterfactual_m3": cf,
            "saved_m3": cf - actual, "area_mu": area, "farmer_count": len(farmers),
            "installed_count": sum(f["status"] == "operating" for f in farmers),
            "committed_count": committed, "installed_rate": sum(f["status"] == "operating" for f in farmers) / len(farmers),
            "commitment_rate": committed / len(farmers), "installed_area_rate": installed_area / area,
            "overuse_ratio": max(0, actual / quota - 1) if quota > 0 else 0,
            "water_price": sum(b["water_price"] for b in bureaus) / len(bureaus),
            "subsidy_rate": sum(b["subsidy_rate"] for b in bureaus) / len(bureaus),
            "bank_rate": sum(b["rate"] for b in banks) / len(banks),
            "loan_cny": sum(b["outstanding_cny"] for b in banks), "overdue_cny": sum(b["overdue_cny"] for b in banks),
            "interest_cny": sum(b["interest_received_cny"] for b in banks),
            "subsidy_cny": sum(f.get("economics", {}).get("subsidy_paid_cny", 0) for f in farmers)}
        metrics[entity].update(relief_cny=sum(f.get('relief_cny', 0) for f in farmers),
                               negative_cash_rate=sum(f['cash_cny'] < 0 for f in farmers) / len(farmers),
                               loss_household_rate=sum(f.get('economics', {}).get('furrow_surplus_cny', f['normal_surplus_cny']) + f.get('economics', {}).get('operating_gain_cny', 0) < 0 for f in farmers) / len(farmers))
    ranked = sorted(state["bureaus"], key=lambda bid: (metrics[bid]["overuse_ratio"], -metrics[bid]["installed_area_rate"], state["bureaus"][bid]["subsidy_spent_cny"], bid))
    for rank, bid in enumerate(ranked, 1):
        state["bureaus"][bid]["rank"] = rank
    state["metrics"] = metrics


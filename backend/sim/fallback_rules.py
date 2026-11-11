"""Explicit demonstration rules. These are not Jev or DeepSeek responses."""
import math


def farmer_rule(f, ctx):
    # Level3 calibrated ERPT: intercept=-.1, npv=.6, peer=.4, skill=.3, risk=.4, sigma=0.
    economic = max(-3, min(3, ctx["cautious_five_season_net_cny"] / 20000))
    peer = ctx["neighbors_operating"] / max(1, ctx["neighbor_count"]) * (.25 + .75 * f["trust"] / 4)
    reserve_penalty = .15 if ctx["cash_after_investment_cny"] < ctx["preferred_cash_buffer_cny"] else 0
    utility = -.1 + .6 * economic + .4 * peer + .3 * f["skill"] / 4 - .4 * f["risk"] / 4 - reserve_penalty + .45 * ctx.get("enthusiasm", 0)
    return {"p_adopt": round(1 / (1 + math.exp(-utility)), 6), "utility": utility,
            "rationale": "离线 ERPT 规则：收益预期保守折减10%，结合五季净收益、邻里信任、技术能力与风险；投资后现金低于两季普通结余时，小幅降低倾向。节水号召通过环境积极性参数增加采纳效用，不增加实际收益。"}


def bureau_rule(facts):
    pressured = facts["forecast_water_m3"] > facts["base_quota_m3"]
    price = round(min(1., facts["previous_water_price"] * (1.1 if pressured else 1)), 4)
    subsidy = min(facts["subsidy_cap"], facts["previous_subsidy_rate"] + (.05 if pressured else 0))
    price = max(price, facts.get("water_price_floor", .1))
    price = min(price, facts.get('water_price_ceiling', 1.))
    subsidy = max(subsidy, facts.get("subsidy_floor", 0))
    delta = facts["forecast_water_m3"] - facts["base_quota_m3"]
    delta = max(-facts["offer_cap_m3"], delta)
    feedback = facts.get('affordability', {})
    adjustment = '；经营承压或干旱缓解，按承受能力上限下调水价（事件下限优先）' if feedback.get('financial_pressure') or feedback.get('drought_easing') else ''
    return {"water_price": price, "subsidy_rate": subsidy, "quota_delta_m3": delta,
            "rationale": f"离线规则：{'预测用水超线，尝试上调水价10%、补贴增加5个百分点（受预算上限限制）' if pressured else '预测未超线，保持政策'}{adjustment}；按本季预测缺口或盈余提出配额互助。"}


def bank_rule(facts):
    rate = round(min(.12, .03 + .04 * (1 - facts["credit_factor"]) + .04 * facts["overdue_ratio"]), 4)
    available = facts["available_cny"]
    approvals = []
    ranked = sorted(facts["applicants"], key=lambda a: (-a["risk_adjusted_surplus_cny"] / max(.01, a["required_loan_cny"] * (.2 + rate)) / a["minimum_coverage"], a["farmer_id"]))
    for a in ranked:
        due = a["required_loan_cny"] * (.2 + rate)
        coverage = a["risk_adjusted_surplus_cny"] / max(.01, due)
        approved = a["cash_cny"] >= 0 and not a["has_arrears"] and coverage >= a["minimum_coverage"] and a["required_loan_cny"] <= available + .001
        if approved:
            available = round(available - a["required_loan_cny"], 2)
        reason = "批准" if approved else "拒绝"
        approvals.append({"farmer_id": a["farmer_id"], "approve": approved,
            "rationale": f"离线规则{reason}：{a['age']}岁，操作能力{a['skill']}/4；能力折减后偿债覆盖率 {coverage:.2f}，要求至少 {a['minimum_coverage']:.2f}。{'有逾期或现金缺口；' if a['has_arrears'] or a['cash_cny'] < 0 else ''}审批后剩余可贷资金 {available:,.2f} 元。"})
    return {"rate": rate, "approvals": approvals,
            "rationale": "离线规则：操作能力影响保守收益，年龄与能力共同确定偿债余量要求；按覆盖率相对要求优先分配授信，兼顾逾期与资金约束。无年龄禁贷线，授信未使用不放款。"}


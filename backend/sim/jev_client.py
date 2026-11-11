from .config import JEV_MODEL


def farmer_facts(f, ctx, tick):
    return {"season": tick, "decision_to_evaluate": "No investment commitment has been made. Choose between investing now and waiting this season.",
        "farmer": {k: f[k] for k in ("id", "age", "area_mu", "risk", "skill", "trust", "confidence", "support", "normal_surplus_cny")},
        "economics": ctx, "actual_credit_offer": f["financing"], "last_two_seasons": f["history"][-2:],
        "decision_preferences": [
            "economics.enthusiasm is a 0-to-1 temporary water-saving campaign motivation. It modestly increases willingness, without adding income, cash, skill, or overriding financing constraints.",
            "The farmer is economically motivated and moderately cautious about an irreversible new investment. Waiting is a normal, available choice, not a failure.",
            "Credit approval only makes investment possible; it does not make the investment profitable or desirable. Do not equate a bank offer with farmer consent.",
            "For the decision, use cautious_expected_gain_cny (90% of the central benefit estimate) and cautious_five_season_net_cny. This modest subjective haircut is already calculated; do not apply it twice.",
            "The two-season preferred_cash_buffer_cny is a preference, not a required deposit or a hard minimum. Normal_surplus_cny is already NET of ordinary farming expenses; do not subtract those expenses or this preferred buffer again.",
            "Under the cash-first rule, a financed investment normally leaves cash_after_investment_cny at zero. This alone is not a financial crisis or a reason to reject an otherwise affordable loan. Evaluate commitment_season_cash_cny (ordinary net operating cash, with no drip gain yet), first_repayment_cny and cautious_operating_cash_after_debt_cny (current conditions held fixed). Do not add loan principal a second time or assume extra credit.",
            "Positive cautious five-season net surplus, non-negative commitment-season cash and positive cautious operating cash after debt can make an approved loan worthwhile. Balance these with actual risk and ability. A non-positive net surplus, a current-season cash deficit, failed financing or insufficient repayment cash favors waiting. Neighbor adoption does not erase an economic loss.",
            "Use the farmer's actual risk, skill, confidence and trust differences. Do not assume future subsidy increases, future water-price increases, guaranteed yields, or that the basin's saving target is personal income."],
        "rules": ["Cash is used first. Credit is approved before this decision; no extra credit can be assumed.",
            "Investment is paid now and subsidy is locked now. No drip gain this season; operation begins next season.",
            "Any loan is repaid in five equal seasonal principal installments from installation, with interest on remaining principal at the quoted seasonal rate.",
            "Five-season net already subtracts investment and loan interest; do not count water savings or drought benefits twice.",
            "Neighbors are observed at the END of the previous season. Future policy is unknown.",
            "Equipment retirement and resale are not modeled. Risk, skill, confidence, support and trust are levels 0 to 4."]}


def payload(facts):
    return {"model": JEV_MODEL, "state": facts, "questions": {"adopt_drip": {"type": "noul", "instructions":
        "Considering this farmer's cautious economic preferences, five-season investment surplus, cash reserve and actual credit terms, does the farmer choose to invest NOW rather than wait this planting season? Available financing alone does not imply adoption. The farmer is still uncommitted, and installation would begin next season."}},
        "provider": {"only": ["typesafe"], "allow_fallbacks": False}}


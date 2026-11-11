from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ScenarioEvent(Model):
    kind: Literal['drought', 'campaign', 'subsidy', 'credit', 'redline']
    season: int = Field(ge=1, le=10)
    end_season: int = Field(ge=1, le=10)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_season < self.season:
            raise ValueError('事件结束季不能早于开始季')
        return self


class Setup(Model):
    bureaus: int = Field(default=3, ge=1, le=6)
    villages_per_bureau: int = Field(default=2, ge=2, le=4)
    farmers_per_village: int = Field(default=16, ge=12, le=18)
    farmers_min: int | None = Field(default=None, ge=12, le=18)
    farmers_max: int | None = Field(default=None, ge=12, le=18)
    events: list[ScenarioEvent] = Field(default_factory=list, max_length=20)
    seed: int = Field(default=20261022, ge=0, le=2147483647)
    initial_cash_factor: float = Field(default=.65, ge=.3, le=1.5)

    @model_validator(mode='after')
    def household_range(self):
        if (self.farmers_min is None) != (self.farmers_max is None):
            raise ValueError('请同时设置农户数量上下限')
        if self.farmers_min is not None and self.farmers_min > self.farmers_max:
            raise ValueError('农户数量下限不能超过上限')
        return self


class Environment(Model):
    scenario: str = "baseline"
    drought: float = Field(default=0., ge=0, le=.8)
    redline_ratio: float = Field(default=.95, ge=.1, le=1.5)
    budget_multiplier: float = Field(default=1., ge=0, le=4)
    credit_factor: float = Field(default=1., ge=0, le=1)
    water_price_floor: float = Field(default=.1, ge=.1, le=1)
    subsidy_floor: float = Field(default=0, ge=0, le=.8)
    enthusiasm: float = Field(default=0, ge=0, le=1)
    instruction: str = Field(default="兼顾农户收益与节水，避免地区用水超线。", max_length=3000)


class TickRequest(Model):
    run_id: str
    expected_tick: int = Field(ge=0)
    mode: Literal["offline", "online"] = "offline"
    environment: Environment


class Loan(Model):
    original_cny: float
    principal_cny: float
    rate: float
    start_tick: int
    principal_arrears_cny: float = 0
    interest_arrears_cny: float = 0
    overdue_seasons: int = 0
    repaid_principal_cny: float = 0
    paid_interest_cny: float = 0


class Farmer(Model):
    id: str
    name: str
    village_id: str
    bureau_id: str
    bank_id: str
    archetype: str
    area_mu: float
    cash_cny: float
    normal_surplus_cny: float
    normal_surplus_per_mu: float = 0
    reference_savings_cny: float = 0
    relief_cny: float = 0
    relief_received_cny: float = 0
    base_gain_cny: float
    equipment_cny: float
    risk: int
    skill: int
    trust: int
    confidence: int
    support: int
    age: int
    neighbors: list[str] = Field(default_factory=list)
    status: Literal["uncommitted", "pending", "operating"] = "uncommitted"
    commitment_tick: int | None = None
    installation_tick: int | None = None
    initial_adopter: bool = False
    loan: Loan | None = None
    decision: dict | None = None
    financing: dict = Field(default_factory=dict)
    loan_request: dict = Field(default_factory=dict)
    economics: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list)
    subsidy_received_cny: float = 0
    investment_cny: float = 0
    water_m3: float = 0


class Bank(Model):
    id: str
    name: str
    village_id: str
    bureau_id: str
    cash_cny: float
    initial_cash_cny: float
    rate: float = .03
    interest_received_cny: float = 0
    principal_received_cny: float = 0
    disbursed_cny: float = 0
    outstanding_cny: float = 0
    overdue_cny: float = 0
    nonperforming_cny: float = 0
    approvals: list[dict] = Field(default_factory=list)
    decision: dict | None = None



class Bureau(Model):
    id: str
    name: str
    villages: list[str] = Field(default_factory=list)
    water_price: float = .20
    subsidy_rate: float = .10
    budget_base_cny: float = 0
    budget_cny: float = 0
    subsidy_spent_cny: float = 0
    relief_budget_cny: float = 0
    relief_spent_cny: float = 0
    base_quota_m3: float = 0
    quota_m3: float = 0
    quota_delta_m3: float = 0
    rank: int = 0
    decision: dict | None = None
    next_policy: dict | None = None
    applied_policy: dict = Field(default_factory=dict)


class Village(Model):
    id: str
    name: str
    bureau_id: str
    bank_id: str
    farmers: list[str] = Field(default_factory=list)
    boundary: dict
    parcels: list[dict]


class BureauDecision(Model):
    water_price: float = Field(ge=.10, le=1.)
    subsidy_rate: float = Field(ge=0, le=.80)
    quota_delta_m3: float
    rationale: str = Field(min_length=1, max_length=3000)


class Approval(Model):
    farmer_id: str
    approve: bool
    rationale: str = Field(min_length=1, max_length=1500)


class BankDecision(Model):
    rate: float = Field(ge=.01, le=.12)
    rationale: str = Field(min_length=1, max_length=3000)
    approvals: list[Approval]


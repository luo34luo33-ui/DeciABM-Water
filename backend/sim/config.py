from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = DEMO_ROOT / "backend" / "logs"
JEV_MODEL = "typesafe/jev-1.14"
LLM_MODEL = "deepseek/deepseek-v4.1-flash"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
BASE_PRICE = .20
WATER_PER_MU = 300
SAVING = .30
FARMER_EXPECTED_GAIN_FACTOR = .90
FARMER_BUFFER_SEASONS = 2
NORMAL_SURPLUS_PER_MU_MIN = 100
NORMAL_SURPLUS_PER_MU_MAX = 150
RELIEF_BUDGET_PER_MU = 30
RELIEF_LOSS_SHARE = .50
CONCURRENCY = 4
PRESETS = {
    "baseline": {"name": "基准", "drought": 0., "redline_ratio": .95, "credit_factor": 1., "budget_multiplier": 1., "instruction": "兼顾农户收益与节水，避免地区用水超线。"},
    "drought": {"name": "干旱", "drought": .30, "redline_ratio": .65, "credit_factor": 1., "budget_multiplier": 1., "instruction": "本季干旱，优先控制区域用水，积极开展配额互助。"},
    "redline": {"name": "红线收紧", "drought": 0., "redline_ratio": .80, "credit_factor": 1., "budget_multiplier": 1., "instruction": "上级收紧用水红线，请加快节水改造，同时控制财政支出。"},
    "credit": {"name": "信贷收紧", "drought": 0., "redline_ratio": .95, "credit_factor": .40, "budget_multiplier": 1., "instruction": "信贷收紧，请评估融资约束，兼顾节水与小农户资金压力。"},
}


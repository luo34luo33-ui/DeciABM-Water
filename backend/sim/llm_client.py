import asyncio
import json
import math

import httpx

from .agent_models import BankDecision, BureauDecision
from .config import CHAT_URL, CONCURRENCY, DECISIONS_URL, JEV_MODEL, LLM_MODEL
from .jev_client import payload as jev_payload

PROMPTS = {
    "bureau": "你是地方水利局Agent。目标：避免区域用水超线，同时兼顾农户收益和补贴财政支出。你可读取共同环境、上级文字指示、其他地区上一季表现；不要假设其他局本季决策已知。配额互助仅调整考核额度，不能降低流域实际用水。返回一个JSON对象且只含 water_price（元/m³，0.10至1.00）、subsidy_rate（0至subsidy_cap）、quota_delta_m3（正为申请、负为转出，负值绝对值不得超过offer_cap_m3）、rationale（简短中文决策依据）。考虑一季安装滞后。不要输出Markdown。",
    "bank": "你是村级农商行Agent。目标是控制坏账并取得利息收入。基于逐户预计偿债现金、贷款差额和既有逾期，决定本季新贷款季利率及每位申请人的授信。授信未使用不放款；贷款从下一季安装后分五季等额还本，利息按剩余本金计算。批准金额为农户所需差额，不可修改；批准总额不得超过available_cny；现金为负或有逾期者不得批准。返回JSON且只含rate（0.01至0.12）、rationale（中文整体依据）、approvals（每个申请人恰好一项，含farmer_id、approve布尔值、rationale中文逐户依据）。没有申请人时返回空数组，仍给出利率。不要输出Markdown。",
}
PROMPTS["bank"] += (
    "正常信贷环境以3%季利率作为绿色设备贷款参考报价，可根据当前风险在允许范围内调整；对偿债覆盖充分且无逾期的申请，应结合利息收入积极评估放贷，不假设农户必须全额自费。"
    "必须逐户结合age年龄、skill操作能力（0低至4高）、technical_support技术支持分析。"
    "年龄是初始化画像，不随种植季机械加一，不设年龄禁贷线，也不能直接把年龄等同于操作能力。"
    "risk_adjusted_surplus_cny已将谨慎收益按操作能力折减；minimum_coverage已包含能力和年龄对应的经营连续性余量。"
    "以你最终选择的季利率计算首季本息required_loan_cny*(0.2+rate)，其与风险调整后现金流的覆盖率必须不低于minimum_coverage，才可批准。"
    "这是Toymodel的演示信贷假设；不要编造退休、家庭代耕或保证人。逐户rationale须说明年龄、操作能力、覆盖率和门槛，条件符合也可基于所给事实谨慎拒贷。"
)
PROMPTS['bureau'] += '水价不得低于water_price_floor；补贴不得低于subsidy_floor（该下限已受预算限制）。这两项是本季上级事件政策约束。'
PROMPTS['bureau'] += '必须结合affordability中的预测亏损户比例、负现金户比例、上季经营结余和干旱缓解信号；水价不得超过water_price_ceiling。承压时优先减轻负担、补贴与配额互助，不能只靠加价。上级事件下限优先于降价。救灾补助由独立限额预算按规则发放，不得假设无限救助或未来收益。'
PROMPTS['bureau'] += '你在农户、银行和本季结算之后决策。必须根据household_feedback中的本季实际采纳、申请、拒贷、经营与财政结果调整政策。water_price与subsidy_rate是policy_effective_tick季生效的下季方案，不得声称改变了已经完成的本季投资；下季仍受届时环境和实际预算约束。quota_delta_m3用于本季用水考核互助复核。其他地方局读取共同本季快照，不知道彼此本次新方案。'
for role in ('bank', 'bureau'):
    PROMPTS[role] += 'response_format是接口参数，不是决策字段；输出中不要添加type或json_object。'


def validate(role, value, facts):
    if role == "farmer":
        p = value["p_adopt"]
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Jev 概率必须为0至1的有限数值")
        return value
    # Some chat outputs echo the response-format marker alongside valid decisions.
    # Remove only this observed metadata; retain all financial and schema checks.
    if isinstance(value, dict) and value.get('type') == 'json_object':
        value = {k: v for k, v in value.items() if k != 'type'}
    if role == "bureau":
        result = BureauDecision.model_validate(value).model_dump()
        if result['water_price'] > facts.get('water_price_ceiling', 1.) + 1e-8:
            raise ValueError('水价超过本季农户承受能力与调价幅度上限')
        if result['water_price'] < facts.get('water_price_floor', .1) - 1e-8 or result['subsidy_rate'] < facts.get('subsidy_floor', 0) - 1e-8:
            raise ValueError('决策低于本季事件规定的水价或补贴下限')
        if result["subsidy_rate"] > facts["subsidy_cap"] + 1e-8:
            raise ValueError("补贴率超过预算允许上限")
        if result["quota_delta_m3"] < -facts["offer_cap_m3"] - .01:
            raise ValueError("配额转出超过允许盈余")
        if result["quota_delta_m3"] > max(0, facts["forecast_water_m3"] - facts["base_quota_m3"]) + .01:
            raise ValueError("配额申请超过本季预测缺口")
        return result
    result = BankDecision.model_validate(value).model_dump()
    candidates = {a["farmer_id"]: a for a in facts["applicants"]}
    ids = [a["farmer_id"] for a in result["approvals"]]
    if len(ids) != len(set(ids)) or set(ids) != set(candidates):
        raise ValueError("审批结果必须与申请人一一对应")
    total = 0
    for approval in result["approvals"]:
        a = candidates[approval["farmer_id"]]
        if approval["approve"]:
            if a["cash_cny"] < 0 or a["has_arrears"]:
                raise ValueError("现金为负或有逾期的申请人不能获批")
            due = a["required_loan_cny"] * (.2 + result["rate"])
            coverage = a["risk_adjusted_surplus_cny"] / max(.01, due)
            if coverage < a["minimum_coverage"]:
                raise ValueError(f"{a['farmer_id']}：{a['age']}岁、操作能力{a['skill']}/4，风险调整后覆盖率{coverage:.3f}低于要求{a['minimum_coverage']:.3f}，不能批准")
            total += a["required_loan_cny"]
    if total > facts["available_cny"] + .005:
        raise ValueError("授信总额超过可贷资金")
    return result


def request_error_message(exc):
    if isinstance(exc, httpx.ConnectTimeout):
        return '连接OpenRouter超时'
    if isinstance(exc, httpx.ConnectError):
        return '连接OpenRouter失败：' + str(exc)
    if isinstance(exc, httpx.ReadTimeout):
        return '等待模型响应超时'
    if isinstance(exc, httpx.RemoteProtocolError):
        return '模型服务响应中断：' + str(exc)
    return str(exc) or type(exc).__name__


class ModelCalls:
    def __init__(self, mode, key, progress, client=None):
        self.mode, self.key, self.progress, self.client = mode, key, progress, client
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self.disabled = ""
        self.traces = []

    def clean(self, value):
        if not self.key:
            return value
        return json.loads(json.dumps(value, ensure_ascii=False).replace(self.key, "[REDACTED]"))

    async def decide(self, role, entity_id, facts, fallback):
        trace = {"id": f"{self.progress['tick']}:{entity_id}", "tick": self.progress["tick"], "entity_id": entity_id,
            "role": role, "model": JEV_MODEL if role == "farmer" else LLM_MODEL,
            "input": facts, "source": "rule", "attempts": [], "raw": None, "result": None, "error": None}
        self.progress["total"] += 1
        async with self.semaphore:
            self.progress["active_entity_ids"].append(entity_id)
            try:
                if self.mode == "online" and not self.disabled:
                    connection_failures = 0
                    for attempt in range(2):
                        body = jev_payload(facts) if role == "farmer" else {
                            "model": LLM_MODEL, "messages": [{"role": "system", "content": PROMPTS[role]},
                                {"role": "user", "content": json.dumps(facts, ensure_ascii=False) +
                                 ("\n上次输出无效，请修正：" + str(trace["error"]) if trace["error"] else "")}],
                            "response_format": {"type": "json_object"}, "reasoning": {"enabled": False},
                            "max_tokens": 6000, "stream": False, "temperature": .2}
                        entry = {"attempt": attempt + 1, "request": body}
                        try:
                            response = await self.client.post(DECISIONS_URL if role == "farmer" else CHAT_URL,
                                json=body, headers={"Authorization": "Bearer " + self.key})
                            connection_failures = 0
                            entry["http_status"] = response.status_code
                            try:
                                raw = response.json()
                            except ValueError:
                                raw = {"unparsed_response": response.text}
                            entry["raw"] = raw
                            trace["raw"] = raw
                            if response.status_code in (401, 402, 403):
                                self.disabled = f"OpenRouter HTTP {response.status_code}：请检查Key、余额或模型访问权限"
                                raise ValueError(self.disabled)
                            response.raise_for_status()
                            if role == "farmer":
                                if not str(raw.get("model", "")).startswith(JEV_MODEL):
                                    raise ValueError("返回的模型不是指定的Jev版本")
                                answer = raw["answers"]["adopt_drip"]
                                if answer["type"] != "noul":
                                    raise ValueError("Jev返回的决策类型不是noul")
                                parsed = {"p_adopt": answer["noul"]}
                            else:
                                choice = raw["choices"][0]
                                if choice.get("finish_reason") != "stop":
                                    raise ValueError("模型输出未完整结束")
                                content = choice["message"]["content"].strip()
                                if content.startswith("```"):
                                    content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                                parsed = json.loads(content)
                            trace["result"] = validate(role, parsed, facts)
                            trace["source"] = "jev" if role == "farmer" else "deepseek"
                            trace["error"] = None
                            break
                        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
                            trace["error"] = request_error_message(exc)
                            entry["error"] = trace["error"]
                            entry['error_type'] = type(exc).__name__
                            entry['error_detail'] = str(exc)
                            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                                connection_failures += 1
                                if connection_failures == 2:
                                    self.disabled = f'连续两次无法建立OpenRouter连接（{type(exc).__name__}），本轮后续调用采用规则'
                            else:
                                connection_failures = 0
                        finally:
                            trace["attempts"].append(entry)
                        if self.disabled:
                            break
                        if attempt == 0:
                            await asyncio.sleep(.5)
                if trace["result"] is None:
                    trace["error"] = trace["error"] or (self.disabled if self.mode == "online" else None)
                    trace["result"] = validate(role, fallback(), facts)
                    if trace["raw"] is None:
                        trace["raw"] = {"rule": "offline-demo", "result": trace["result"]}
                safe = self.clean(trace)
                self.traces.append(safe)
                return safe
            finally:
                self.progress["active_entity_ids"].remove(entity_id)
                self.progress["completed"] += 1


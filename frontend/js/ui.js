export const money=v=>Number(v||0).toLocaleString('zh-CN',{maximumFractionDigits:2});
export const pct=v=>`${(Number(v||0)*100).toFixed(1)}%`;
export const statusName={uncommitted:'未承诺投资',pending:'已承诺，等待安装',operating:'设备运行中'};
export const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const row=(label,value)=>`<div class="detail-row"><span>${escape(label)}</span><strong>${escape(value)}</strong></div>`;
const section=(title,body)=>`<section class="detail-section"><h3>${escape(title)}</h3>${body}</section>`;
const source=trace=>`<span class="source-label ${trace?.source==='rule'?'rule':''}">${trace?.source==='human'?'用户实际选择':trace?.source==='rule'?'离线规则':trace?.source==='jev'?'Jev 原生输出':trace?.source==='deepseek'?'DeepSeek 决策':'尚无模型决策'}</span>`;
const raw=trace=>trace?`<details><summary>查看请求与原始响应</summary><pre>${escape(JSON.stringify(trace,null,2))}</pre></details>`:'';
const rationale=trace=>trace?`${source(trace)}<p class="rationale">${escape(trace.result?.rationale||'Jev 返回数值倾向，不生成解释文本。')}</p>${trace.error?`<p class="form-error">${escape(trace.error)}</p>`:''}${raw(trace)}`:'<p class="muted">第0季为初始化，还没有产生本季决策。</p>';
const button=(id,label,tail='')=>`<button data-pick="${escape(id)}"><span>${escape(label)}</span><span>${escape(tail)}</span></button>`;

export function renderInspector(container,state,selected,pick){
  let html='';
  const f=state.farmers[selected],b=state.banks[selected],bureau=state.bureaus[selected],v=state.villages[selected];
  if(f){
    const c=f.decision?.input?.economics||f.economics||{}, loan=f.loan;
    const cautiousRows=c.cautious_five_season_net_cny===undefined?'':row('谨慎预期五季净收益',`${money(c.cautious_five_season_net_cny)} 元`)+row('期望保留现金',`${money(c.preferred_cash_buffer_cny)} 元`);
    const p=f.decision?.result?.p_adopt;
    html=`<h2>${escape(f.name)}</h2><p class="muted">${escape(state.villages[f.village_id].name)}，${escape(statusName[f.status])}</p>`;
    html+=section('经营与投资',row('经营面积',`${f.area_mu.toFixed(1)} 亩`)+row('现金余额',`${money(f.cash_cny)} 元`)+row('设备报价',`${money(f.equipment_cny)} 元`)+row('当季运行增量收益',`${money(f.economics?.operating_gain_cny)} 元`)+row('首次投资季',f.commitment_tick===null?'尚未投资':`第 ${f.commitment_tick} 季`)+row('安装运行季',f.installation_tick===null?'尚未安排':`第 ${f.installation_tick} 季`));
    const e=f.economics||{};
    html+=section('本季经营收支',row('正常季经营净结余',`${money(f.normal_surplus_cny)} 元`)+row('干旱损失',`${money(e.drought_loss_cny)} 元`)+row('相对基准新增水费',`${money(e.water_extra_cny)} 元`)+row('本季救灾补助',`${money(f.relief_cny)} 元`)+row('累计救灾补助',`${money(f.relief_received_cny)} 元`)+row('本季经营结余（不含补助）',e.furrow_surplus_cny===undefined?'尚未结算':`${money(e.furrow_surplus_cny+(e.operating_gain_cny||0))} 元`)+`<p class="muted">正常结余已扣基准水费；设备增量收益、投资和本息分别结算。补助有预算，不保证消除亏损。</p>`);
    html+=section('采纳倾向',f.decision?.source==='human'?`<p>用户在第${f.decision.tick}季选择${f.decision.result.adopt?'安装':'等待'}。</p>${source(f.decision)}`:p===undefined?`<p class="muted">${f.initial_adopter?'初始安装者无新增投资决策。':'尚未执行采纳决策。'}</p>`:
      `<div class="probability"><span>采纳 ${pct(p)}</span><span>暂缓 ${pct(1-p)}</span></div><svg class="probability-bar" viewBox="0 0 100 8" role="img" aria-label="采纳倾向 ${pct(p)}"><rect class="wait" width="100" height="8"></rect><rect class="adopt" width="${p*100}" height="8"></rect></svg><p class="muted">第 ${f.decision.tick} 季决策；阈值 0.5，实际投资还需融资可行。</p>${source(f.decision)}`);
    html+=section('融资条件',row('决策时资金缺口',c.required_loan_cny===undefined?'尚未计算':`${money(c.required_loan_cny)} 元`)+row('五季投资净收益',c.five_season_net_cny===undefined?'尚未计算':`${money(c.five_season_net_cny)} 元`)+cautiousRows+row('贷款本金余额',`${money(loan?.principal_cny)} 元`)+row('合同季利率',loan?pct(loan.rate):'无贷款')+row('逾期本息',`${money((loan?.principal_arrears_cny||0)+(loan?.interest_arrears_cny||0))} 元`)+`<p class="rationale">${escape(f.financing?.rationale||(f.status==='uncommitted'?'尚未进行本季授信评估。':'已有设备或投资承诺，本季无需新授信；贷款按原合同偿还。'))}</p>`+button(f.bank_id,'查看所属农商行'));
    html+=section('画像与邻里',row('年龄（初始画像）',`${f.age} 岁`)+row('设备操作能力',`${f.skill} / 4`)+row('风险厌恶',`${f.risk} / 4`)+row('收益信心 / 技术支持',`${f.confidence} / ${f.support}（0—4）`)+row('固定邻居',`${f.neighbors.length} 户`)+row('决策时已安装邻居',`${c.neighbors_operating??'—'} 户`)+row('邻里信任',`${f.trust}（0—4）`));
    const assessment=f.financing?.assessment;
    if(assessment)html+=section('银行风险评估',row('风险调整后季结余',`${money(assessment.risk_adjusted_surplus_cny)} 元`)+row('偿债覆盖率 / 要求',`${assessment.coverage.toFixed(2)} / ${assessment.minimum_coverage.toFixed(2)}`)+`<p class="muted">收益按操作能力折减，年龄与能力影响偿债余量要求；无年龄禁贷线。</p>`);
    html+=raw(f.decision);
  }else if(b){
    html=`<h2>${escape(b.name)}</h2><p class="muted">${escape(state.bureaus[b.bureau_id].name)}，第 ${state.tick} 季</p>`;
    html+=section('资金与信贷',row('可用资金',`${money(b.cash_cny)} 元`)+row('新贷款季利率',pct(b.rate))+row('贷款本金余额',`${money(b.outstanding_cny)} 元`)+row('逾期本息',`${money(b.overdue_cny)} 元`)+row('不良贷款本金',`${money(b.nonperforming_cny)} 元`)+row('累计实收利息',`${money(b.interest_received_cny)} 元`));
    html+=section('本季决策依据',rationale(b.decision));
    html+=section('逐户审批',b.approvals.length?`<ul class="approval-list">${b.approvals.map(a=>`<li>${button(a.farmer_id,state.farmers[a.farmer_id].name,a.approve?`批准 ${money(a.amount_cny)} 元`:'拒贷')}<p>${escape(a.rationale)}</p>${a.assessment?`<p>${a.assessment.age}岁，操作能力${a.assessment.skill}/4；覆盖率${a.assessment.coverage.toFixed(2)}，要求${a.assessment.minimum_coverage.toFixed(2)}</p>`:''}${a.approve?`<p>${a.used?'已放款':'授信未使用，未放款'}</p>`:''}</li>`).join('')}</ul>`:'<p class="muted">本季没有贷款申请。</p>');
  }else if(bureau){
    const m=state.metrics[bureau.id];
    html=`<h2>${escape(bureau.name)}</h2><p class="muted">第 ${bureau.rank} 名，按超线比例、安装面积及补贴支出排序</p>`;
    html+=section('政策与财政',row('水价',`${bureau.water_price.toFixed(3)} 元/m³`)+row('设备补贴',pct(bureau.subsidy_rate))+row('本季补贴预算',`${money(bureau.budget_cny)} 元`)+row('本季补贴支出',`${money(bureau.subsidy_spent_cny)} 元`));
    if(bureau.next_policy)html+=section('下一季政策方案',row('生效季',`第 ${bureau.next_policy.effective_tick} 季`)+row('拟执行水价',`${money(bureau.next_policy.water_price)} 元/m³`)+row('拟执行补贴',pct(bureau.next_policy.subsidy_rate))+`<p class="muted">本季结果向上反馈后制定，下一季仍受届时事件与实际预算约束。</p>`);
    html+=section('农户承受能力与救灾',row('经营亏损户比例',pct(m.loss_household_rate))+row('负现金户比例',pct(m.negative_cash_rate))+row('本季救灾预算',`${money(bureau.relief_budget_cny)} 元`)+row('本季救灾支出',`${money(bureau.relief_spent_cny)} 元`)+row('本次方案水价上限',bureau.decision?.input?.water_price_ceiling===undefined?'历史记录未提供':`${money(bureau.decision.input.water_price_ceiling)} 元/m³`));
    html+=section('用水与互助',row('本季用水',`${money(m.water_m3)} m³`)+row('基础配额',`${money(bureau.base_quota_m3)} m³`)+row('互助净调入',`${money(bureau.quota_delta_m3)} m³`)+row('调整后配额',`${money(bureau.quota_m3)} m³`)+row('超线比例',pct(m.overuse_ratio)));
    html+=section(bureau.next_policy?'本季反馈与下季政策依据':'本季决策依据',rationale(bureau.decision));
    html+=section('辖区村庄',`<ul class="farmer-list">${bureau.villages.map(id=>`<li>${button(id,state.villages[id].name,`${state.metrics[id].installed_count}户运行`)}</li>`).join('')}</ul>`);
  }else if(v){
    const m=state.metrics[v.id];
    html=`<h2>${escape(v.name)}</h2><p class="muted">${escape(state.bureaus[v.bureau_id].name)}，合成田块与邻接</p>`;
    html+=section('村庄概况',row('本季用水',`${money(m.water_m3)} m³`)+row('设备节水估算',`${money(m.saved_m3)} m³`)+row('承诺 / 安装',`${m.committed_count} / ${m.installed_count} 户`)+row('安装面积占比',pct(m.installed_area_rate))+row('总经营面积',`${money(m.area_mu)} 亩`)+button(v.bank_id,'查看本村农商行'));
    html+=section('农户',`<ul class="farmer-list">${v.farmers.map(id=>`<li>${button(id,state.farmers[id].name,statusName[state.farmers[id].status])}</li>`).join('')}</ul>`);
  }else if(selected.startsWith('transfer:')){
    const t=state.quota_transfers.find(t=>`transfer:${t.id}`===selected);
    if(t)html=`<h2>配额互助记录</h2><p class="muted">第 ${state.tick} 季的实际撮合</p>${section('成交明细',row('转出方',state.bureaus[t.from].name)+row('接收方',state.bureaus[t.to].name)+row('转出方申报供给',`${money(t.offered_m3)} m³`)+row('接收方申请需求',`${money(t.requested_m3)} m³`)+row('本笔成交',`${money(t.volume_m3)} m³`))}<p class="rationale">互助调整考核配额，不改变流域实际用水。流域配额总量保持不变。</p>`;
  }else{
    const m=state.metrics.basin;
    html=`<h2>流域管理局</h2><p class="muted">由你扮演，向地方水利局下达指示</p>`;
    html+=section('本季环境',row('供水减少',pct(state.environment.drought))+row('全流域红线',`${money(m.redline_m3)} m³`)+row('新授信资金比例',pct(state.environment.credit_factor))+row('补贴预算倍数',`${state.environment.budget_multiplier} 倍`)+`<p class="rationale">${escape(state.environment.instruction)}</p>`);
    html+=section('地区表现',`<ul class="farmer-list">${Object.values(state.bureaus).sort((a,b)=>a.rank-b.rank).map(b=>`<li>${button(b.id,b.name,`第${b.rank}名`)}</li>`).join('')}</ul>`);
  }
  container.innerHTML=html||'<h2>该季没有此项记录</h2><p>请选择其他节点。</p>';
  container.querySelectorAll('[data-pick]').forEach(el=>el.addEventListener('click',()=>pick(el.dataset.pick)));
}

import {escape as esc, money, pct} from './ui.js';
import {renderCharts} from './chart.js';

const $=id=>document.getElementById(id), d3=window.d3;
const fields=[['age','年龄',18,80,'年龄参与银行的经营连续性评估，不设年龄禁贷线。'],['skill','操作能力',0,4,'影响经营结余和银行对设备收益的保守判断。'],['risk','风险厌恶',0,4,'影响Jev对不可逆投资的倾向；你的选择仍由你决定。'],['confidence','收益信心',0,4,'影响Jev对收益预期的判断，不直接增加真实收入。'],['support','技术支持',0,4,'作为银行与Jev判断的背景，不凭空提供资金。'],['trust','邻里信任',0,4,'影响Jev如何看待已经安装的邻居。']];
const actionName={adopt:'安装节水设施',wait:'继续等待',continue:'继续经营'};
const statusName={uncommitted:'尚未承诺投资',pending:'已承诺，下一季安装',operating:'节水设备运行中'};
let draft,selected,profile={},view,busy=false,revealed=false,replayBundle=null,replayIndex=0,chosenAction=null,pollTimer;
let autoRunning=false,stopRequested=false;
const basinCache=new Map();

async function api(path,body,keyOverride){
  const headers={};
  if(body)headers['Content-Type']='application/json';
  const key=keyOverride===undefined?$('game-key').value.trim():keyOverride;
  if(key)headers.Authorization=`Bearer ${key}`;
  const response=await fetch(path,body?{method:'POST',headers,body:JSON.stringify(body)}:{headers});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
  return data;
}
function notice(text,error=false){$('game-notice').hidden=!text;$('game-notice').textContent=text;$('game-notice').classList.toggle('error',error);}
const row=(label,value)=>`<div class="ledger-row"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
const ledger=rows=>`<dl class="ledger">${rows}</dl>`;
function rawDetails(trace){return trace?`<details class="document"><summary>查看对照原始记录</summary><pre>${esc(JSON.stringify(trace,null,2))}</pre></details>`:'';}
function chapter(tick,stage){return stage==='choice'?`第 ${tick} 季，开灌之前`:tick===0?'从一块田，开始你的十季':`第 ${tick} 季，卷宗已归档`;}
function header(title,subtitle,count='十季之间'){return `<div class="chapter-head"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div><span class="chapter-count">${esc(count)}</span></div>`;}

function drawMap(village,farmerId,neighbors=[]){
  const svg=d3.select('#role-map');if(svg.empty())return;
  const data={type:'FeatureCollection',features:village.parcels};
  const path=d3.geoPath(d3.geoIdentity().reflectY(true).fitExtent([[12,12],[388,198]],data));
  const ids=new Set(neighbors.map(n=>n.id));
  for(const parcel of village.parcels){
    const id=parcel.properties.farmer_id;
    svg.append('path').attr('d',path(parcel)).attr('class',id===farmerId?'own':ids.has(id)?'neighbor':'')
      .append('title').text(id===farmerId?'你的田块':ids.has(id)?'固定邻居田块':'本村田块');
    if(id===farmerId){const bounds=path.bounds(parcel);if(bounds[1][0]-bounds[0][0]>46&&bounds[1][1]-bounds[0][1]>24){const [x,y]=path.centroid(parcel);svg.append('text').attr('x',x).attr('y',y+4).text('我的田');}}
  }
}

function roster(){
  view=null;replayBundle=null;$('replay-controls').hidden=true;
  $('game-mode').textContent=draft.mode==='online'?'沿用在线模型':'沿用离线规则';
  const f=draft.farmers.find(f=>f.id===selected),v=draft.villages[f.village_id];
  const summary=draft.summary;
  $('game-root').innerHTML=header('先选一个身份，再打开你的家底','这一局，你只决定自己的节水投资。村庄与其他主体继续独立模拟。')+
    `<div class="roster-layout"><section><div class="recommended-roles">${draft.recommendations.map(r=>{const p=draft.farmers.find(f=>f.id===r.farmer_id);return `<button class="identity-card ${selected===p.id?'selected':''}" data-role="${esc(p.id)}" aria-pressed="${selected===p.id}"><strong>${esc(r.label)}</strong><span>${p.area_mu.toFixed(1)}亩</span><small>${esc(draft.villages[p.village_id].name)}，${p.age}岁，操作${p.skill}/4</small></button>`;}).join('')}</div>
    <div class="role-picker"><label>选择村庄<select id="role-village">${Object.entries(draft.villages).map(([id,v])=>`<option value="${esc(id)}" ${id===f.village_id?'selected':''}>${esc(v.name)}</option>`).join('')}</select></label><label>或浏览本村身份<select id="role-farmer">${draft.farmers.filter(p=>p.village_id===f.village_id).map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)}，${p.area_mu.toFixed(1)}亩</option>`).join('')}</select></label></div>
    <div class="profile-sheet"><div class="profile-title"><h2>写下你的角色画像</h2><button id="reset-profile">恢复原始画像</button></div><div class="profile-sliders">${fields.map(([key,label,min,max,help])=>`<label>${label}<output id="value-${key}" for="profile-${key}">${profile[key]}${key==='age'?'岁':' / 4'}</output><input id="profile-${key}" data-profile="${key}" type="range" min="${min}" max="${max}" step="1" value="${profile[key]}"><small>${help}</small></label>`).join('')}</div></div>
    <div class="roster-start"><p class="small-note">家底在确认后独立抽签：8,000～36,000元，每档1,000元。没有现金滑条，也不能在本局重抽。</p><button id="start-role" class="primary">确认身份，抽取家底</button></div></section>
    <aside class="parcel-sheet"><h2>${esc(v.name)}，${esc(f.name)}</h2><svg id="role-map" class="parcel-map" viewBox="0 0 400 210" role="img" aria-label="所选农户的真实合成田块"></svg><p class="small-note">绿色是你的田。合成空间，非真实行政地图。</p>${ledger(row('经营面积',`${f.area_mu.toFixed(1)}亩`)+row('设备报价',`${money(f.equipment_cny)}元`)+row('正常季经营净结余',`${money(estimatedSurplus(f))}元`)+row('初始现金','家底待揭晓'))}<p class="small-note">沿用已保存配置：${summary.bureaus}个地方局、${summary.villages}个村、${summary.farmers}户，种子${summary.seed}。除你以外的农户保持原有画像。未来事件不提前揭晓。</p></aside></div>`;
  drawMap(v,selected);
  document.querySelectorAll('[data-role]').forEach(el=>el.onclick=()=>pickRole(el.dataset.role));
  $('role-village').onchange=e=>pickRole(draft.farmers.find(p=>p.village_id===e.target.value).id);
  $('role-farmer').onchange=e=>pickRole(e.target.value);
  $('reset-profile').onclick=()=>pickRole(selected);
  document.querySelectorAll('[data-profile]').forEach(input=>input.oninput=()=>{profile[input.dataset.profile]=Number(input.value);$('value-'+input.dataset.profile).value=input.value+(input.dataset.profile==='age'?'岁':' / 4');const rows=document.querySelectorAll('.parcel-sheet .ledger-row dd');rows[2].textContent=`${money(estimatedSurplus(f))}元`;});
  $('start-role').onclick=()=>operate(async()=>{view=await api('/api/farmer/start',{draft_id:draft.draft_id,farmer_id:selected,profile});revealed=false;render();});
}
function estimatedSurplus(f){return Math.round(f.area_mu*(100+50*(.6*profile.skill/4+.4*Math.max(0,Math.min(1,(f.reference_savings_cny-500)/1300))))*100)/100;}
function pickRole(id){selected=id;const f=draft.farmers.find(p=>p.id===id);profile=Object.fromEntries(fields.map(([k])=>[k,f[k]]));roster();}

function envelope(){
  const f=view.player,investment=f.equipment_cny*(1-view.bureau.subsidy_rate);
  $('game-root').innerHTML=header('你的十季，从这封家底信开始',`${view.village.name}，${f.age}岁的${f.name}。身份已确认，资金只抽一次。`)+
  `<section class="envelope-scene"><div class="envelope-copy"><h2>${f.area_mu.toFixed(1)}亩田，怎样度过十季？</h2><p>你会收到真实的天气、政策与银行回函。可以安装，也可以等一等；其他农户照常作自己的决定。</p>${revealed?`${ledger(row('随机初始现金',`${money(view.initial_cash_cny)}元`)+row('按初始补贴估算的投资',`${money(investment)}元`)+row('现金 / 设备净投资',pct(view.initial_cash_cny/investment)))}<button id="first-season" class="primary">迎接第一季</button>`:'<p class="small-note">点击右边信封，揭晓本局家底。刷新不会改变金额。</p>'}</div><button id="cash-envelope" class="cash-envelope" ${revealed?'disabled':''} aria-label="揭开家底信封"><span class="seal">${revealed?'启':'封'}</span><strong>${revealed?'本局家底':'拆开家底信封'}</strong>${revealed?`<span id="cash-amount" class="cash-amount">${money(view.initial_cash_cny)}元</span>`:'<span>8,000～36,000元，开局抽签</span>'}</button></section>`;
  $('cash-envelope').onclick=()=>{revealed=true;render();};
  if($('first-season'))$('first-season').onclick=prepare;
}

function resultSection(){
  const turn=view.last_turn;if(!turn)return '';
  const e=turn.economics,comparison=turn.comparison,p=comparison?.result?.p_adopt;
  return `<section class="result-sheet"><h2>第 ${turn.tick} 季，你选择了${actionName[turn.action]}</h2><div class="result-grid"><div>${ledger(row('本季经营结余（不含补助）',`${money(e.furrow_surplus_cny+e.operating_gain_cny)}元`)+row('救灾到账',`${money(e.relief_cny)}元`)+row('设备投资实付',`${money(e.investment_paid_cny)}元`)+row('贷款实际到账',`${money(e.draw_cny)}元`)+row('本季归还本金 / 利息',`${money(e.principal_paid_cny)} / ${money(e.interest_paid_cny)}元`)+row('季末现金',`${money(turn.cash_cny)}元`))}${turn.reason?`<p class="small-note">你留的话：${esc(turn.reason)}</p>`:''}</div><div>${p===undefined?'<h3>沿着自己的选择继续经营</h3><p class="small-note">本季没有新的采纳决定，也没有重复询问Jev。</p>':`<h3>${comparison.source==='rule'?'规则对照':'Jev对照'}，现在揭晓</h3><p>采纳倾向 ${pct(p)}，等待倾向 ${pct(1-p)}。</p><div class="comparison-bars" role="img" aria-label="采纳倾向${pct(p)}"><span style="width:${p*100}%"></span></div><p>${(p>=.5)===(turn.action==='adopt')?'这一次，你们的选择一致。':'这一次，你作出了不同的选择。'}</p><p class="small-note">对照基于你提交前相同的画像、经济与真实授信条件，不影响你的选择，也不代表另一条人生的实际收益。</p>${rawDetails(comparison)}`}</div></div></section>`;
}

function desk(){
  const f=view.player,c=view.context,b=view.bureau,choice=view.stage==='choice'&&!replayBundle;
  const needChoice=f.status==='uncommitted';
  const installed=view.neighbors.filter(n=>n.status==='operating').length;
  const env=view.environment;
  const title=env.drought>0?'旱情来信':b.subsidy_rate>=.5?'补贴已经落地':'这一季，水来到田头';
  $('game-root').innerHTML=header(chapter(view.tick,view.stage),`${view.village.name}，${statusName[f.status]}${replayBundle?'，只读回放':''}`,`第 ${view.tick} / 10 季`)+
    `<div class="decision-desk"><section class="desk-panel"><h2>我的田与家底</h2><svg id="role-map" class="parcel-map" viewBox="0 0 400 210" role="img" aria-label="本人和固定邻居的田块"></svg><p class="small-note">绿色为本人，浅黄色为固定邻居；邻里信息取上一季末。</p>${ledger(row('现金余额',`${money(f.cash_cny)}元`)+row('初始家底',`${money(view.initial_cash_cny)}元`)+row('正常季净结余',`${money(f.normal_surplus_cny)}元`)+row('年龄 / 操作能力',`${f.age}岁 / ${f.skill}档`)+row('贷款本金余额',`${money(f.loan?.principal_cny)}元`)+row('逾期本息',`${money(view.summary.overdue_cny)}元`))}</section>
    <section class="desk-panel"><h2>本季卷宗</h2><details class="document" open><summary>${title}</summary><p class="document-label">天气与供水通知</p><p>${env.drought>0?`供水比正常减少${pct(env.drought)}。本季干旱损失参考${money(c.drought_loss_cny)}元。`:'本季供水未因干旱减少。'}运行中的设备按同供水条件节水30%。</p></details>
    <details class="document" open><summary>${esc(b.name)}的公告</summary><p>本季水价 ${money(b.water_price)}元/m³，设备补贴 ${pct(b.subsidy_rate)}。</p>${b.next_policy?`<p class="small-note">已公布第${b.next_policy.effective_tick}季方案：水价${money(b.next_policy.water_price)}元，补贴${pct(b.next_policy.subsidy_rate)}；执行时仍受当季事件及预算约束。</p>`:''}</details>
    <details class="document" open><summary>银行回函</summary><p>${esc(f.financing.rationale||(needChoice?'新一季尚未开卷，授信将在准备后确定。':'已有承诺或设备，本季无需新授信，原贷款继续按合同偿还。'))}</p></details>
    <details class="document"><summary>田埂上的消息</summary><p>截至上一季末，${view.neighbors.length}位固定邻居中有${installed}户设备运行。</p><p class="small-note">${view.neighbors.map(n=>`${esc(n.name)}：${statusName[n.status]}`).join('；')||'这块田暂时没有共享田界的邻居。'}</p></details></section>
    <section class="desk-panel choice-panel"><h2>${choice?'做出这一季的选择':'下一季，继续你的故事'}</h2>${choice&&needChoice?`${ledger(row('设备报价',`${money(f.equipment_cny)}元`)+row('补贴后的净投资',`${money(c.investment_cny)}元`)+row('优先使用本人现金',`${money(Math.max(0,c.investment_cny-c.required_loan_cny))}元`)+row('融资缺口',`${money(c.required_loan_cny)}元`)+row('真实授信金额',`${money(f.financing.amount_cny)}元`)+row('贷款合同季利率',c.required_loan_cny>0?pct(c.rate):'自费，无贷款')+row('五季投资净收益参考',`${money(c.five_season_net_cny)}元`))}<p class="small-note">本季投资，下一季安装运行。收益参考假设当前环境保持不变，未来事件未知；Jev对照在提交后揭晓。</p>${!c.financing_feasible?'<p class="choice-blocked">现金不足或未获足额授信，本季暂不能安装。仍可等待后续环境变化。</p>':''}<div class="choice-actions"><button id="choose-adopt" class="primary" ${!c.financing_feasible?'disabled':''}>安装节水设施</button><button id="choose-wait">本季继续等待</button></div>`:choice?`<p>${f.status==='pending'?'投资已承诺，安装将按时激活。':'设备继续运行，查看本季收益与还贷。'}</p><div class="choice-actions"><button id="continue-season" class="primary">结算本季经营</button></div>`:`<p>本季账目已归档。打开下一季卷宗，查看届时的天气、政策与授信。</p><div class="choice-actions"><button id="next-season" class="primary" ${replayBundle?'disabled':''}>打开第 ${view.tick+1} 季卷宗</button></div>`}<p class="small-note">你只控制自己的投资，其他农户、银行和地方局继续独立模拟。</p>${!needChoice&&!replayBundle?`<div class="choice-actions"><button id="run-remaining">一键运行剩余 ${10-view.committed_tick} 季</button></div>`:''}</section></div>${resultSection()}${seasonIndex()}`;
  drawMap(view.village,f.id,view.neighbors);
  if($('choose-adopt'))$('choose-adopt').onclick=()=>confirmChoice('adopt');
  if($('choose-wait'))$('choose-wait').onclick=()=>confirmChoice('wait');
  if($('continue-season'))$('continue-season').onclick=()=>submitChoice('continue');
  if($('next-season'))$('next-season').onclick=prepare;
  if($('run-remaining'))$('run-remaining').onclick=runRemaining;
  bindReplay();
}
function seasonIndex(){return `<div class="season-index" aria-label="个人季节回放">${view.turns.map(t=>`<button data-replay="${t.tick}">第${t.tick}季 ${actionName[t.action]}</button>`).join('')}</div>`;}
function ending(){
  const f=view.player,s=view.summary;
  const statement=s.overdue_cny>0?'十季走完了，账本里仍有未付本息。你的田和债务，都值得在下一次选择前认真回看。':f.status==='uncommitted'?'你把等待保留到了最后。环境一直在变，你也始终保留了自己的判断。':f.status==='pending'?'你在最后一季作出了承诺。设备尚未运行，收益还在未来，投资与可能的债务已经记入账本。':'水流过了十季，你的设备已经运行。看一看节水、现金与债务，哪些结果符合你最初的打算？';
  $('game-root').innerHTML=header('十季之后，合上你的田间账本',`${view.village.name}，${f.name}的结局卷宗`,'第 10 / 10 季')+`<nav class="ending-navigation" aria-label="十季结局范围"><a href="#personal-outcome">我的经营账本</a><a href="#basin-outcome">全流域多主体结局</a></nav><section id="personal-outcome" class="ending-sheet"><p class="ending-statement">${statement}</p>${ledger(row('开局随机家底',`${money(view.initial_cash_cny)}元`)+row('期末现金 / 相对开局变化',`${money(f.cash_cny)} / ${money(s.cash_change_cny)}元`)+row('首次投资',f.commitment_tick===null?'始终等待':`第${f.commitment_tick}季`)+row('设备状态',statusName[f.status])+row('累计实际设备节水',`${money(s.cumulative_saved_m3)}m³`)+row('剩余贷款本金',`${money(s.loan_principal_cny)}元`)+row('逾期本息',`${money(s.overdue_cny)}元`))}<p class="small-note">期末现金变化不是单独的投资利润；剩余本金仍需未来偿还。节水仅计设备实际运行季，不将干旱减少的供水计为设备贡献。</p><div class="choice-actions"><button data-export class="primary">带走我的卷宗</button><button data-new>再过一次十季</button></div>${seasonIndex()}</section>${resultSection()}`;
  $('game-root').insertAdjacentHTML('beforeend',`<section id="basin-outcome" class="basin-outcome" data-run="${esc(view.run_id)}"><h2>十季之后，整个流域怎样了？</h2><p class="small-note">正在读取本局地方局、银行、村庄和其他农户的完整记录。</p></section>`);
  loadBasin(view.run_id);
  document.querySelector('[data-export]').onclick=exportGame;document.querySelector('[data-new]').onclick=newGame;bindReplay();
}

function basinUrl(runId,{tick=10,scope='basin',node=scope,diagram='topology',chart='water'}={}){
  return `sandbox.html#${new URLSearchParams({run:runId,round:String(tick),scope,node,view:diagram,chart})}`;
}
function basinLink(runId,id,name,scope='basin',diagram='topology'){
  return `<a href="${basinUrl(runId,{scope,node:id,diagram})}" target="_blank" rel="noopener">${esc(name)}</a>`;
}
function resultTable(head,body){return `<div class="basin-table-wrap"><table class="basin-table"><thead><tr>${head.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;}
async function loadBasin(runId){
  let box=$('basin-outcome');
  try{
    if(!basinCache.has(runId))basinCache.set(runId,api(`/api/runs/${encodeURIComponent(runId)}`));
    const bundle=await basinCache.get(runId);
    if(!$('basin-outcome')||$('basin-outcome').dataset.run!==runId)return;
    box=$('basin-outcome');
    const state=bundle.snapshots.find(s=>s.tick===10);
    if(!state||state.experience?.type!=='farmer')throw new Error('尚未找到本局第10季完整快照');
    const m=state.metrics.basin,player=state.experience.player_id;
    const link=basinUrl(runId);
    box.innerHTML=`<div class="basin-heading"><div><h2>第10季，全流域多主体结局</h2><p>你是${m.farmer_count}户中的一户，其他农户沿用仿真配置。以下均来自本局实际记录。</p></div><a class="basin-open" href="${link}" target="_blank" rel="noopener">查看本局多主体拓扑与完整回放</a></div>
      <div class="basin-totals">${ledger(row('已运行户数',`${m.installed_count} / ${m.farmer_count}户（${pct(m.installed_rate)}）`)+row('已运行面积占比',pct(m.installed_area_rate)))}${ledger(row('本季实际用水',`${money(m.water_m3)}m³`)+row('用水红线',`${money(m.redline_m3)}m³`))}${ledger(row('本季设备节水',`${money(m.saved_m3)}m³`)+row('流域超线比例',pct(m.overuse_ratio)))}</div>
      <section class="basin-section"><h3>地方水利局：政策、考核与采纳</h3><p class="small-note">展示第10季实际执行政策；点击局名可查看决策依据、下一季方案与配额互助。</p>${resultTable(['排名 / 地方局','运行户数','实际用水 / 配额 m³','超线','水价 元/m³','设备补贴','本季补贴 / 救灾 元'],Object.values(state.bureaus).sort((a,b)=>a.rank-b.rank).map(b=>{const t=state.metrics[b.id];return `<tr><td>第${b.rank}名 ${basinLink(runId,b.id,b.name,b.id)}</td><td>${t.installed_count}/${t.farmer_count}</td><td>${money(t.water_m3)} / ${money(t.redline_m3)}</td><td>${pct(t.overuse_ratio)}</td><td>${money(b.water_price)}</td><td>${pct(b.subsidy_rate)}</td><td>${money(b.subsidy_spent_cny)} / ${money(b.relief_spent_cny)}</td></tr>`;}).join(''))}</section>
      <section class="basin-section"><h3>各村庄：谁完成了改造？</h3>${resultTable(['村庄','运行 / 总户数','运行面积','本季用水 m³','贷款余额 元'],Object.values(state.villages).map(v=>{const t=state.metrics[v.id];return `<tr><td>${basinLink(runId,v.id,v.name,v.id)}</td><td>${t.installed_count}/${t.farmer_count}</td><td>${pct(t.installed_area_rate)}</td><td>${money(t.water_m3)}</td><td>${money(t.loan_cny)}</td></tr>`;}).join(''))}</section>
      <section class="basin-section"><h3>农商行：授信与风险的十季账本</h3><p class="small-note">批准和拒贷为第1～10季申请次数，同一农户跨季重复申请分别计数；获批不等于实际放款。</p>${resultTable(['银行','累计批准 / 拒贷 次','累计放款 元','累计利息 元','贷款余额 元','逾期本息 元','不良本金 元'],Object.values(state.banks).map(b=>{let approved=0,rejected=0;for(const snap of bundle.snapshots.filter(s=>s.tick>0&&s.tick<=10)){for(const a of snap.banks[b.id].approvals){if(a.approve)approved++;else rejected++;}}return `<tr><td>${basinLink(runId,b.id,b.name,b.village_id)}</td><td>${approved} / ${rejected}</td><td>${money(b.disbursed_cny)}</td><td>${money(b.interest_received_cny)}</td><td>${money(b.outstanding_cny)}</td><td>${money(b.overdue_cny)}</td><td>${money(b.nonperforming_cny)}</td></tr>`;}).join(''))}</section>
      <section class="basin-section"><h3>其他农户：逐户查看真实结果</h3><div class="basin-filters"><label>村庄<select id="basin-village-filter"><option value="all">全部村庄</option>${Object.values(state.villages).map(v=>`<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('')}</select></label><label>查找农户<input id="basin-farmer-search" type="search" placeholder="农户编号或村名"></label></div><p class="small-note">你的记录置顶；窄屏可横向查看，首列固定。</p><div id="basin-farmers"></div><div class="basin-filters"><button id="basin-farmers-prev">上一页</button><span id="basin-farmer-count" class="small-note"></span><button id="basin-farmers-next">下一页</button></div></section>
      <section class="basin-section"><h3>多主体反馈如何逐季发生</h3><div class="basin-chart-tabs" role="group" aria-label="全流域趋势"><button data-basin-chart="water" class="selected">用水</button><button data-basin-chart="adoption">采纳</button><button data-basin-chart="policy">政策</button><button data-basin-chart="credit">信贷</button></div><p class="small-note">点击曲线，在完整工作台中回放对应季节。工作台为本局只读记录，不会覆盖当前全模拟。</p><div id="basin-charts" class="charts"></div></section>`;
    let farmerPage=0;
    const showFarmers=()=>{
      const village=$('basin-village-filter').value,query=$('basin-farmer-search').value.trim().toLowerCase();
      const farmers=Object.values(state.farmers).filter(f=>(village==='all'||f.village_id===village)&&`${f.id} ${f.name} ${state.villages[f.village_id].name}`.toLowerCase().includes(query)).sort((a,b)=>Number(b.id===player)-Number(a.id===player));
      $('basin-farmers').innerHTML=farmers.length?resultTable(['农户 / 村庄','状态','投资季','期末现金 元','贷款本金 元','逾期本息 元'],farmers.slice(farmerPage*12,(farmerPage+1)*12).map(f=>`<tr class="${f.id===player?'player-result':''}"><td>${basinLink(runId,f.id,`${f.name}${f.id===player?'（你）':''}`,f.village_id,'parcels')}<small>${esc(state.villages[f.village_id].name)}</small></td><td>${statusName[f.status]}${f.initial_adopter?'（初始安装）':''}</td><td>${f.commitment_tick===null?'未投资':f.commitment_tick===0?'初始':`第${f.commitment_tick}季`}</td><td>${money(f.cash_cny)}</td><td>${money(f.loan?.principal_cny)}</td><td>${money((f.loan?.principal_arrears_cny||0)+(f.loan?.interest_arrears_cny||0))}</td></tr>`).join('')):'<p class="small-note">没有符合条件的农户。</p>';
      $('basin-farmer-count').textContent=`共${farmers.length}户，第${farmerPage+1}/${Math.max(1,Math.ceil(farmers.length/12))}页`;
      $('basin-farmers-prev').disabled=farmerPage===0;$('basin-farmers-next').disabled=(farmerPage+1)*12>=farmers.length;
    };
    $('basin-farmers-prev').onclick=()=>{farmerPage--;showFarmers();};$('basin-farmers-next').onclick=()=>{farmerPage++;showFarmers();};
    $('basin-village-filter').onchange=$('basin-farmer-search').oninput=()=>{farmerPage=0;showFarmers();};showFarmers();
    const showChart=group=>renderCharts($('basin-charts'),state.history,'basin',group,10,tick=>window.open(basinUrl(runId,{tick,chart:group}),'_blank','noopener'));
    document.querySelectorAll('[data-basin-chart]').forEach(el=>el.onclick=()=>{document.querySelectorAll('[data-basin-chart]').forEach(button=>button.classList.toggle('selected',button===el));showChart(el.dataset.basinChart);});
    showChart('water');
  }catch(e){
    basinCache.delete(runId);
    if($('basin-outcome')?.dataset.run===runId){box.innerHTML=`<h2>本局全流域结果暂未载入</h2><p>${esc(e.message)}</p><button id="retry-basin">重新读取</button>`;$('retry-basin').onclick=()=>loadBasin(runId);}
  }
}
function render(){
  if(!view)return;
  $('game-mode').textContent=view.mode==='online'?'在线模型，十季体验':'离线规则，十季体验';
  if(view.tick===0&&!replayBundle)envelope();else if(view.stage==='finished')ending();else desk();
  $('replay-controls').hidden=!replayBundle;
  if(busy)lockActions();
  if(replayBundle){$('replay-label').textContent=`只读回放 第${view.tick}季`;$('replay-prev').disabled=replayIndex===0;$('replay-next').disabled=replayIndex===replayBundle.snapshots.length-1;}
}

function lockActions(){
  document.querySelectorAll('#game-root button,#new-game,#records-open,#key-open').forEach(el=>el.disabled=true);
}
async function runRemaining(){
  if(busy||replayBundle||view.player.status==='uncommitted'||view.committed_tick>=10||needKey())return;
  await operate(async()=>{$('game-progress').hidden=false;poll();await advanceRemaining();});
}
async function advanceRemaining(){
  const key=$('game-key').value.trim(),start=view.committed_tick,total=10-start;
  autoRunning=true;stopRequested=false;
  $('stop-remaining').hidden=false;$('stop-remaining').disabled=false;$('stop-remaining').textContent='本季结束后停止';
  try{
    while(view.committed_tick<10&&!stopRequested){
      $('game-batch').textContent=`自动经营：已完成 ${view.committed_tick-start}/${total} 季，正在执行第 ${view.committed_tick+1} 季`;
      if(view.stage!=='choice')view=await api('/api/farmer/prepare',{run_id:view.run_id,expected_tick:view.committed_tick},key);
      if(view.player.status==='uncommitted')throw new Error('仍需你亲自决定是否采纳，自动经营已停止');
      view=await api('/api/farmer/decide',{run_id:view.run_id,expected_tick:view.committed_tick,action:'continue',reason:''},key);
      render();
      if(view.committed_tick<10&&!stopRequested)await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    }
    notice(view.committed_tick===10?'已完成剩余季，十季结局和逐季卷宗已保存。':`已在第${view.committed_tick}季结束后停止，可继续逐季查看或再次运行剩余季。`);
  }finally{autoRunning=false;$('stop-remaining').hidden=true;}
}

async function operate(fn){
  if(busy)return;busy=true;notice('');
  lockActions();
  try{await fn();}catch(e){notice(e.message,true);if(view){try{const live=await api('/api/farmer/state');if(live.active){view=live;replayBundle=null;}}catch{}}}
  finally{busy=false;clearTimeout(pollTimer);$('game-progress').hidden=true;$('new-game').disabled=$('records-open').disabled=$('key-open').disabled=false;$('stop-remaining').hidden=true;$('game-batch').textContent='';if(view)render();else if(draft)roster();}
}
function needKey(){if(view.mode==='online'&&!$('game-key').value.trim()){$('key-dialog').showModal();$('game-key').focus();return true;}return false;}
async function poll(){
  if(!busy)return;
  try{const p=await api('/api/farmer/progress');$('game-phase').textContent=p.phase;$('game-count').textContent=`${p.completed} / ${p.total}`;$('game-meter').max=Math.max(1,p.total);$('game-meter').value=p.completed;}catch{$('game-phase').textContent='等待后端响应';}
  if(busy)pollTimer=setTimeout(poll,650);
}
async function prepare(){
  if(busy||replayBundle||needKey())return;
  await operate(async()=>{$('game-progress').hidden=false;poll();view=await api('/api/farmer/prepare',{run_id:view.run_id,expected_tick:view.committed_tick});});
}
function confirmChoice(action){
  chosenAction=action;const c=view.context;
  $('choice-title').textContent=action==='adopt'?'确认安装节水设施':'这一季，继续等待';
  $('choice-summary').innerHTML=action==='adopt'?ledger(row('本季支付净投资',`${money(c.investment_cny)}元`)+row('其中贷款',`${money(c.required_loan_cny)}元`)+row('贷款季利率',c.required_loan_cny>0?pct(c.rate):'无贷款')+row('设备开始运行',`第${view.tick+1}季`)):'<p>本季不支付设备投资，也不使用设备贷款授信。你仍会经历当季的经营收支、环境变化和政策反馈。</p>';
  $('adopt-auto-option').hidden=action!=='adopt'||view.tick>=10;$('auto-after-adopt').checked=true;
  $('choice-reason').value='';$('choice-dialog').showModal();
}
async function submitChoice(action,reason='',runAfter=false){
  if(busy||replayBundle||needKey())return;
  await operate(async()=>{$('game-progress').hidden=false;poll();view=await api('/api/farmer/decide',{run_id:view.run_id,expected_tick:view.committed_tick,action,reason});revealed=true;render();if(runAfter&&view.player.status!=='uncommitted'&&view.committed_tick<10)await advanceRemaining();});
}
async function newGame(){
  if(busy)return;
  if(view&&!window.confirm('新开一局会结束当前可继续的角色体验，已归档季节仍保留。确认重新选角吗？'))return;
  await operate(async()=>{draft=await api('/api/farmer/preview',{});view=null;revealed=false;pickRole(draft.recommendations[0].farmer_id);});
}
async function historyFor(runId){return await api(`/api/farmer/history${runId?'?run_id='+encodeURIComponent(runId):''}`);}
function bindReplay(){document.querySelectorAll('[data-replay]').forEach(el=>el.onclick=async()=>{if(busy)return;try{if(!replayBundle)replayBundle=await historyFor(view.run_id);replayIndex=replayBundle.snapshots.findIndex(s=>s.tick===Number(el.dataset.replay));view=replayBundle.snapshots[replayIndex];render();}catch(e){notice(e.message,true);}});}
async function exportGame(){
  try{const data=replayBundle||await historyFor(view?.run_id);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`水到田头-${data.run_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message,true);}
}
$('stop-remaining').onclick=()=>{if(autoRunning){stopRequested=true;$('stop-remaining').disabled=true;$('stop-remaining').textContent='将在本季结束后停止';}};
$('key-open').onclick=()=>$('key-dialog').showModal();
$('toggle-game-key').onclick=()=>{const show=$('game-key').type==='password';$('game-key').type=show?'text':'password';$('toggle-game-key').textContent=show?'隐藏':'显示';$('toggle-game-key').setAttribute('aria-pressed',show);$('toggle-game-key').setAttribute('aria-label',show?'隐藏Key':'显示Key');};
$('key-dialog').addEventListener('close',()=>{$('game-key').type='password';$('toggle-game-key').textContent='显示';$('toggle-game-key').setAttribute('aria-pressed','false');$('toggle-game-key').setAttribute('aria-label','显示Key');});
$('key-form').onsubmit=e=>{e.preventDefault();$('key-dialog').close();notice('Key已留在本页内存。');};
$('choice-form').onsubmit=e=>{e.preventDefault();const reason=$('choice-reason').value,runAfter=chosenAction==='adopt'&&$('auto-after-adopt').checked;$('choice-dialog').close();submitChoice(chosenAction,reason,runAfter);};
document.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>$(el.dataset.close).close());
$('new-game').onclick=newGame;
$('export-game').onclick=exportGame;
$('replay-prev').onclick=()=>{view=replayBundle.snapshots[--replayIndex];render();};
$('replay-next').onclick=()=>{view=replayBundle.snapshots[++replayIndex];render();};
$('back-live').onclick=async()=>{try{const live=await api('/api/farmer/state');if(!live.active){view=null;replayBundle=null;await newGame();}else{view=live;replayBundle=null;render();}}catch(e){notice(e.message,true);}};
$('records-open').onclick=async()=>{
  if(busy)return;$('records-dialog').showModal();$('game-records').textContent='正在读取已归档卷宗';
  try{const runs=await api('/api/farmer/runs');$('game-records').innerHTML=runs.length?runs.map(r=>`<div class="record-row"><span>${esc(r.player_name)}，第${r.last_tick}季<br><small>${esc(r.run_id)}</small></span><button data-record="${esc(r.run_id)}">回看</button></div>`).join(''):'还没有角色卷宗，确认身份后开始记录。';document.querySelectorAll('[data-record]').forEach(el=>el.onclick=async()=>{try{replayBundle=await historyFor(el.dataset.record);replayIndex=replayBundle.snapshots.length-1;view=replayBundle.snapshots[replayIndex];$('records-dialog').close();render();}catch(e){notice(e.message,true);}});}catch(e){$('game-records').textContent=e.message;}
};
try{const live=await api('/api/farmer/state');if(live.active){view=live;render();}else await newGame();}catch(e){notice(`卷宗暂时无法打开：${e.message}。恢复连接后刷新页面重试。`,true);}

import {Topology} from './topology.js';
import {renderCharts} from './chart.js';
import {renderInspector, money, pct, escape} from './ui.js';

const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.hash.slice(1));
const view={scope:params.get('scope')||'basin',selected:params.get('node')||'basin',
  layer:['management','credit','quota'].includes(params.get('layer'))?params.get('layer'):'management',
  diagram:['parcels','atlas'].includes(params.get('view'))?params.get('view'):'topology',regionFilter:params.get('region')||'all',
  chart:['water','adoption','policy','credit'].includes(params.get('chart'))?params.get('chart'):'water'};
let config, draft, latest, bundle, displayed, busy=false, external=false;
let stopRequested=false, batchCompleted=0, batchTarget=1;
const eventNames={drought:'干旱冲击',campaign:'节水号召',subsidy:'补贴落地',credit:'信贷收紧',redline:'红线收紧'};
const eventNotes={drought:'供水减少40%，水价至少0.50元，红线65%',campaign:'节水积极性提高至0.8',subsidy:'补贴目标55%，预算提高至2.5倍',credit:'新授信资金比例降至40%',redline:'红线降至正常沟灌的80%'};
const presetEvents=[{kind:'campaign',season:2,end_season:10},{kind:'drought',season:3,end_season:4},{kind:'subsidy',season:5,end_season:7},{kind:'credit',season:7,end_season:8},{kind:'redline',season:9,end_season:10}];
const fieldHelp={
  drought:'当季可用供水相对正常季减少的比例，影响农户取水量和干旱损失。',
  'redline-ratio':'区域用水考核额度占正常全沟灌用水的比例。数值越低，节水目标越严格。',
  'budget-multiplier':'地方局每季补贴预算为辖区全部设备报价的25%乘以该倍数。倍数越大，可用于设备补贴的资金越多。',
  'credit-factor':'银行季初现金中可用于本季新授信的比例。提高后可支持更多贷款申请，银行根据各户偿债能力审批。',
  instruction:'以流域管理局身份下达指示。地方局结合指示、预算和农户经营状况，制定下季水价与补贴。',
  'setup-bureaus':'设置1～6个地方水利局，各局独立制定政策。多局时可开展配额互助。',
  'setup-villages':'每个水利局管辖2～4个村庄，每村设一家银行。改变后影响总规模和空间布局。',
  'setup-min':'每村农户数量的随机下限，包含两户初始安装者。各村在上下限之间独立抽取。',
  'setup-max':'每村农户数量的随机上限。与下限相同时，各村户数固定。',
  'setup-cash-factor':'新局初始现金为原画像现金乘以该系数。默认65%，对应5,200～23,400元；系数越低，设备融资缺口越大。农户扮演者的家底独立抽签。',
  'setup-seed':'控制村庄户数、田块和农户画像抽样。相同种子与参数生成相同初始流域；玩家家底抽签独立。'
};
function helpTitle(label,text){
  const title=document.createElement('span');title.className='field-label';title.tabIndex=0;
  title.dataset.help=text;title.textContent=label.firstChild.textContent.trim();label.firstChild.replaceWith(title);
}
Object.entries(fieldHelp).forEach(([id,text])=>helpTitle($(id).closest('label'),text));
const eventHeading=document.querySelector('.event-heading h3');
eventHeading.tabIndex=0;eventHeading.classList.add('field-label');eventHeading.dataset.help='为新局安排10季内的环境事件。勾选启用，起止季均计入持续期，多项事件可叠加。';
const fieldTooltip=$('field-tooltip');let helpTarget;
function hideHelp(){
  if(fieldTooltip.matches(':popover-open'))fieldTooltip.hidePopover();
  helpTarget?.removeAttribute('aria-describedby');helpTarget=null;
}
function showHelp(target){
  hideHelp();helpTarget=target;target.setAttribute('aria-describedby','field-tooltip');
  fieldTooltip.textContent=target.dataset.help;fieldTooltip.showPopover();
  const r=target.getBoundingClientRect(),tip=fieldTooltip.getBoundingClientRect();
  fieldTooltip.style.left=`${Math.max(16,Math.min(r.left,innerWidth-tip.width-16))}px`;
  fieldTooltip.style.top=`${Math.max(16,r.bottom+8+tip.height<innerHeight-16?r.bottom+8:r.top-tip.height-8)}px`;
}
document.addEventListener('pointerover',e=>{const t=e.target.closest('[data-help]');if(t&&!t.contains(e.relatedTarget))showHelp(t);});
document.addEventListener('pointerout',e=>{const t=e.target.closest('[data-help]');if(t&&!t.contains(e.relatedTarget))hideHelp();});
document.addEventListener('focusin',e=>{if(e.target.matches('[data-help]'))showHelp(e.target);});
document.addEventListener('focusout',e=>{if(e.target===helpTarget)hideHelp();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideHelp();});
document.addEventListener('scroll',hideHelp,true);window.addEventListener('resize',hideHelp);
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('close',hideHelp));
const topology=new Topology($('topology'),pick);

async function api(path,options={}){
  const response=await fetch(path,options);
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));
  return data;
}
function notice(text,error=false){$('notice').hidden=!text;$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function entityName(id){return displayed?.bureaus[id]?.name||displayed?.villages[id]?.name||'全流域';}
function scopeOfSelection(){return displayed.metrics[view.scope]?view.scope:'basin';}
function isReplay(){return external||displayed.tick!==latest.tick;}
function remember(){
  const hash=new URLSearchParams({scope:view.scope,node:view.selected,layer:view.layer,view:view.diagram,region:view.regionFilter,chart:view.chart,round:String(displayed.tick),run:displayed.run_id});
  history.replaceState(null,'',`#${hash}`);
}
function pick(id){
  view.selected=id;
  if(view.diagram==='atlas'&&!id.startsWith('transfer:')){view.scope='basin';render();return;}
  if(id==='basin'){view.scope='basin';view.diagram='topology';}
  else if(displayed.bureaus[id]){view.scope=id;view.diagram='topology';}
  else if(displayed.villages[id]){view.scope=id;}
  else if(displayed.banks[id]){view.scope=displayed.banks[id].village_id;}
  else if(displayed.farmers[id]){view.scope=displayed.farmers[id].village_id;}
  render();
}
function validView(){
  if(!displayed.villages[view.scope]&&!displayed.bureaus[view.scope])view.scope='basin';
  if(view.diagram==='atlas')view.scope='basin';
  else if(!displayed.villages[view.scope])view.diagram='topology';
  if(view.regionFilter!=='all'&&!displayed.bureaus[view.regionFilter])view.regionFilter='all';
  if(!['basin',...Object.keys(displayed.bureaus),...Object.keys(displayed.villages),...Object.keys(displayed.banks),...Object.keys(displayed.farmers),...displayed.quota_transfers.map(t=>`transfer:${t.id}`)].includes(view.selected))view.selected=view.scope;
}
function render(){
  validView();
  const replay=isReplay(),scope=scopeOfSelection(),m=displayed.metrics[scope];
  $('round-label').textContent=`${replay?'回放 ':''}第 ${displayed.tick} 季`;
  $('scope-label').textContent=entityName(scope);
  $('water-value').textContent=money(Math.round(m.water_m3));
  $('redline-value').textContent=money(Math.round(m.redline_m3));
  $('redline-status').textContent=m.overuse_ratio>0?`超线 ${pct(m.overuse_ratio)}`:'用水未超线';
  $('adoption-value').textContent=pct(m.installed_area_rate);
  $('adoption-caption').textContent=`${m.installed_count} / ${m.farmer_count} 户已运行`;
  $('tick-button').disabled=busy||replay;
  const length=Number($('run-length').value);
  $('tick-button').textContent=busy?'正在执行…':length===1?'执行下一季':`连续运行 ${length} 季`;
  $('run-length').disabled=busy||replay;
  ['settings-open','key-open','setup-open','logs-open'].forEach(id=>$(id).disabled=busy);
  $('stop-button').hidden=!busy||batchTarget===1;
  $('stop-button').disabled=stopRequested;
  $('stop-button').textContent=stopRequested?'将在本季结束后停止':'本季完成后停止';
  $('scenario').disabled=busy||replay;
  $('mode').disabled=busy||replay;
  $('new-run').disabled=busy;
  const atlas=view.diagram==='atlas';
  $('view-parcels').disabled=!displayed.villages[view.scope];
  $('view-atlas').classList.toggle('selected',atlas);$('view-atlas').setAttribute('aria-pressed',atlas);
  $('atlas-controls').hidden=!atlas;
  $('atlas-region').innerHTML='<option value="all">全部地方局</option>'+Object.values(displayed.bureaus).map(b=>`<option value="${escape(b.id)}">${escape(b.name)}</option>`).join('');
  $('atlas-region').value=view.regionFilter;
  $('focus-village').disabled=!selectedVillage();
  $('view-topology').classList.toggle('selected',view.diagram==='topology');
  $('view-topology').setAttribute('aria-pressed',view.diagram==='topology');
  $('view-parcels').classList.toggle('selected',view.diagram==='parcels');
  $('view-parcels').setAttribute('aria-pressed',view.diagram==='parcels');
  document.querySelectorAll('[data-layer]').forEach(el=>{const on=el.dataset.layer===view.layer;el.classList.toggle('selected',on);el.setAttribute('aria-pressed',on);el.disabled=atlas;});
  document.querySelectorAll('[data-chart]').forEach(el=>{const on=el.dataset.chart===view.chart;el.classList.toggle('selected',on);el.setAttribute('aria-selected',on);});
  const breadcrumbs=$('breadcrumbs');breadcrumbs.innerHTML='<button data-home>全流域</button>';
  const village=displayed.villages[view.scope], bureau=village?displayed.bureaus[village.bureau_id]:displayed.bureaus[view.scope];
  if(bureau)breadcrumbs.insertAdjacentHTML('beforeend',`<span>/</span><button data-bureau="${escape(bureau.id)}">${escape(bureau.name)}</button>`);
  if(village)breadcrumbs.insertAdjacentHTML('beforeend',`<span>/</span><span>${escape(village.name)}</span>`);
  breadcrumbs.querySelector('[data-home]').onclick=()=>pick('basin');
  breadcrumbs.querySelector('[data-bureau]')?.addEventListener('click',()=>pick(bureau.id));
  topology.render(displayed,view);
  renderInspector($('inspector'),displayed,view.selected,pick);
  $('graph-hint').textContent=atlas?'全流域统计口径；选中田块查看详情，可聚焦所属村庄':view.layer==='quota'?'点击地区间的配额连线查看成交；互助不改变流域总用水':village?view.diagram==='parcels'?'点击田块查看农户，选中农户可见固定邻居':'点击农户或银行查看决策，可切换田块视图':'点击水利局或村庄聚焦，拖动与滚轮缩放';
  $('graph-empty').hidden=!(view.layer==='quota'&&!atlas&&displayed.quota_transfers.length===0);
  $('graph-empty').textContent='本季尚无配额互助成交';
  const last=bundle.snapshots.at(-1);
  $('chart-scope').textContent=entityName(scope);
  renderCharts($('charts'),last.history,scope,view.chart,displayed.tick,showTick);
  $('timeline').max=last.tick;$('timeline').value=displayed.tick;$('timeline').disabled=busy;
  $('timeline-label').textContent=`第 ${displayed.tick} 季`;
  $('previous-tick').disabled=busy||displayed.tick===bundle.snapshots[0].tick;
  $('next-tick').disabled=busy||displayed.tick===last.tick;
  $('return-live').disabled=busy||!replay;
  const events=(displayed.active_events||[]).map(e=>eventNames[e.kind]);
  $('run-source').textContent=events.length?`本季事件：${events.join('、')}`:replay?'只读回放':'逐季保存运行记录';
  updateDraftSummary();remember();
}
function updateDraftSummary(){
  const next=latest.tick+1,events=(latest.setup.events||[]).filter(e=>e.season<=next&&e.end_season>=next);
  $('environment-summary').textContent=events.length?`第 ${next} 季事件：${events.map(e=>eventNames[e.kind]).join('、')}`:`下一季：供水减少 ${pct(draft.drought)}，红线 ${pct(draft.redline_ratio)}，授信 ${pct(draft.credit_factor)}`;
}
function selectedVillage(){return displayed?.villages[view.selected]?.id||displayed?.farmers[view.selected]?.village_id||displayed?.banks[view.selected]?.village_id;}
let resizeTimer;
window.addEventListener('resize',()=>{
  clearTimeout(resizeTimer);
  resizeTimer=setTimeout(()=>{if(displayed)renderCharts($('charts'),bundle.snapshots.at(-1).history,scopeOfSelection(),view.chart,displayed.tick,showTick);},120);
});
function showTick(tick){
  if(busy)return;
  const found=bundle.snapshots.find(s=>s.tick===tick);
  if(!found)return;
  displayed=found;render();
  notice(isReplay()?`正在只读回放 ${displayed.run_id} 的第 ${tick} 季。返回当前状态后可继续执行。`:'');
}
async function loadLive(){
  latest=await api('/api/state');
  bundle=await api(`/api/runs/${encodeURIComponent(latest.run_id)}`);
  displayed=latest;external=false;
  render();
}
function fillSettings(){
  $('drought').value=Math.round(draft.drought*100);$('redline-ratio').value=Math.round(draft.redline_ratio*100);
  $('budget-multiplier').value=draft.budget_multiplier;$('credit-factor').value=Math.round(draft.credit_factor*100);
  $('instruction').value=draft.instruction;
  $('settings-error').textContent='';
}
function eventRow(event={kind:'drought',season:3,end_season:4},enabled=true){
  const row=document.createElement('div');row.className='event-row';
  row.innerHTML=`<label class="event-enabled"><input type="checkbox" ${enabled?'checked':''} aria-label="启用此事件"></label><label>事件<select class="event-kind">${Object.entries(eventNames).map(([k,n])=>`<option value="${k}">${n}</option>`).join('')}</select></label><label>开始季<input class="event-start" type="number" min="1" max="10" value="${event.season}" required></label><label>结束季<input class="event-end" type="number" min="1" max="10" value="${event.end_season}" required></label><button type="button" class="event-remove" aria-label="删除此事件">删除</button><p class="field-help event-note"></p>`;
  row.querySelector('select').value=event.kind;
  const update=()=>{row.querySelector('.event-note').textContent=eventNotes[row.querySelector('select').value];row.classList.toggle('event-muted',!row.querySelector('[type=checkbox]').checked);};
  helpTitle(row.querySelector('.event-kind').closest('label'),'选择环境冲击类型。下方列出真实参数变化；当季有多个事件时叠加生效。');
  helpTitle(row.querySelector('.event-start').closest('label'),'事件首次生效的季数，范围1～10。');
  helpTitle(row.querySelector('.event-end').closest('label'),'事件持续到这一季，包含本季。到期后该事件的环境约束结束。');
  row.addEventListener('change',update);row.querySelector('button').onclick=()=>row.remove();update();$('event-list').append(row);
}
function fillSetup(){
  const s=latest.setup;
  $('setup-bureaus').value=s.bureaus;$('setup-villages').value=s.villages_per_bureau;
  $('setup-min').value=s.farmers_min??12;$('setup-max').value=s.farmers_max??18;$('setup-seed').value=s.seed;
  $('setup-cash-factor').value=s.initial_cash_factor??.65;
  $('event-list').replaceChildren();(s.events?.length?s.events:presetEvents).forEach(e=>eventRow(e));
  $('setup-error').textContent='';updateSetup();
}
function updateSetup(changed){
  if(Number($('setup-min').value)>Number($('setup-max').value))$(changed==='setup-max'?'setup-min':'setup-max').value=$(changed||'setup-min').value;
  document.querySelectorAll('.setup-sliders input').forEach(input=>document.querySelector(`output[for="${input.id}"]`).value=input.id==='setup-cash-factor'?`${Math.round(Number(input.value)*100)}%`:input.value);
  const villages=Number($('setup-bureaus').value)*Number($('setup-villages').value);
  $('setup-summary').textContent=`${villages} 个村庄，约 ${villages*Number($('setup-min').value)}～${villages*Number($('setup-max').value)} 户；每村两户初始安装者。`;
}
async function poll(){
  if(!busy)return;
  try{
    const progress=await api('/api/progress');
    if(busy){
      $('batch-label').textContent=`已完成 ${batchCompleted}/${batchTarget} 季，正在执行第 ${latest.tick+1} 季`;
      $('progress-label').textContent=progress.phase;
      $('progress-count').textContent=`${progress.completed} / ${progress.total}`;
      $('tick-progress').max=Math.max(1,progress.total);$('tick-progress').value=progress.completed;
      topology.activity(progress.active_entity_ids);
    }
  }catch{if(busy)$('progress-label').textContent='等待后端响应…';}
  if(busy)setTimeout(poll,650);
}
async function execute(){
  if(busy||isReplay())return;
  if($('mode').value==='online'&&!$('api-key').value.trim()){
    $('key-dialog').showModal();$('api-key').focus();return;
  }
  const runMode=$('mode').value,environment=structuredClone(draft),key=$('api-key').value.trim();
  batchTarget=Number($('run-length').value);batchCompleted=0;stopRequested=false;
  busy=true;notice('');$('progress-region').hidden=false;
  $('batch-label').textContent=`已完成 0/${batchTarget} 季`;
  render();poll();
  let stoppedForService=false;
  try{
    const headers={'Content-Type':'application/json'};
    if(runMode==='online')headers.Authorization=`Bearer ${key}`;
    for(let i=0;i<batchTarget;i++){
      if(i>0&&stopRequested)break;
      const next=await api('/api/tick',{method:'POST',headers,body:JSON.stringify({run_id:latest.run_id,expected_tick:latest.tick,mode:runMode,environment})});
      latest=next;bundle.snapshots.push(next);displayed=next;batchCompleted++;
      const summary=next.call_summary;
      $('batch-label').textContent=`已完成 ${batchCompleted}/${batchTarget} 季`;
      render();
      if(runMode==='online'&&summary.online_unavailable){stoppedForService=true;notice(`${summary.notice}。本批次已在第 ${next.tick} 季结束后停止。`);break;}
      if(stopRequested)break;
      if(i+1<batchTarget)await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    }
    if(!stoppedForService){
      notice(`${stopRequested?'已停止':'运行完成'}：完成 ${batchCompleted}/${batchTarget} 季，决策详情已保存到运行记录。`);
    }
  }catch(error){
    try{await loadLive();notice(`运行已停止：${error.message}。已重新读取服务端保存状态，不自动重发。`,true);}
    catch{notice(`运行已停止：${error.message}。请恢复连接后返回当前状态。`,true);}
  }
  finally{busy=false;$('progress-region').hidden=true;render();}
}
async function openRuns(){
  $('logs-dialog').showModal();$('runs-list').textContent='正在读取运行记录…';
  try{
    const runs=await api('/api/runs');
    $('runs-list').innerHTML=runs.length?runs.map(r=>`<div class="run-row"><div>${escape(r.run_id)}<small>${r.setup.bureaus}局 / ${r.setup.villages_per_bureau}村每局 / ${r.setup.farmers_min??r.setup.farmers_per_village}～${r.setup.farmers_max??r.setup.farmers_per_village}户每村，已保存到第${r.last_tick}季</small></div><button data-run="${escape(r.run_id)}">回放</button></div>`).join(''):'尚无保存记录。执行一季后将自动保存。';
    $('runs-list').querySelectorAll('[data-run]').forEach(el=>el.onclick=async()=>{
      try{await replay(await api(`/api/runs/${encodeURIComponent(el.dataset.run)}`));$('logs-dialog').close();}catch(e){notice(e.message,true);}
    });
  }catch(e){$('runs-list').textContent=e.message;}
}
async function replay(data){
  if(!Array.isArray(data.snapshots)||!data.snapshots.length||!data.snapshots[0].metrics?.basin||!data.snapshots[0].farmers)throw new Error('请选择本工作台导出的运行 JSON。');
  bundle=data;external=true;displayed=bundle.snapshots[0];view.scope=view.selected='basin';view.diagram='topology';render();
  notice(`只读回放 ${data.run_id}。使用底部时间轴查看各季，返回当前后可继续仿真。`);
}

$('tick-button').onclick=execute;
$('run-length').onchange=()=>render();
$('stop-button').onclick=()=>{stopRequested=true;render();};
$('scenario').onchange=()=>{const preset=config.presets[$('scenario').value];if(preset)draft={scenario:$('scenario').value,...Object.fromEntries(Object.entries(preset).filter(([k])=>k!=='name'))};updateDraftSummary();notice('场景已设置，将在下一季生效。');};
$('mode').onchange=()=>notice($('mode').value==='online'?'在线模式将通过 OpenRouter 调用 Jev 与 DeepSeek；失败会标注规则代用。':'离线规则模式不调用 API，所有结果标记为规则。');
$('settings-open').onclick=()=>{fillSettings();$('settings-dialog').showModal();};
$('key-open').onclick=()=>$('key-dialog').showModal();
$('toggle-api-key').onclick=()=>{const show=$('api-key').type==='password';$('api-key').type=show?'text':'password';$('toggle-api-key').textContent=show?'隐藏':'显示';$('toggle-api-key').setAttribute('aria-pressed',show);$('toggle-api-key').setAttribute('aria-label',show?'隐藏Key':'显示Key');};
$('key-dialog').addEventListener('close',()=>{$('api-key').type='password';$('toggle-api-key').textContent='显示';$('toggle-api-key').setAttribute('aria-pressed','false');$('toggle-api-key').setAttribute('aria-label','显示Key');});
$('key-form').onsubmit=e=>{e.preventDefault();$('key-dialog').close();notice('Key 已保存到当前页面内存。');};
$('setup-open').onclick=()=>{fillSetup();$('setup-dialog').showModal();};
document.querySelectorAll('.setup-sliders input').forEach(el=>el.oninput=()=>updateSetup(el.id));
$('add-event').onclick=()=>eventRow();
$('preset-events').onclick=()=>{$('event-list').replaceChildren();presetEvents.forEach(e=>eventRow(e));};
document.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>$(el.dataset.close).close());
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
$('settings-form').onsubmit=event=>{
  event.preventDefault();
  draft={scenario:'custom',drought:Number($('drought').value)/100,redline_ratio:Number($('redline-ratio').value)/100,
    budget_multiplier:Number($('budget-multiplier').value),credit_factor:Number($('credit-factor').value)/100,instruction:$('instruction').value};
  $('scenario').value='custom';$('settings-dialog').close();updateDraftSummary();notice('设置已保存到页面内存，将在下一季使用。');
};
$('setup-form').onsubmit=async event=>{
  event.preventDefault();
  const ids=['setup-bureaus','setup-villages','setup-min','setup-max','setup-seed','setup-cash-factor'];
  if(!ids.every(id=>$(id).reportValidity()))return;
  try{
    $('new-run').disabled=true;
    const events=[...document.querySelectorAll('.event-row')].filter(row=>row.querySelector('[type=checkbox]').checked).map(row=>({kind:row.querySelector('select').value,season:Number(row.querySelector('.event-start').value),end_season:Number(row.querySelector('.event-end').value)}));
    if(events.some(e=>e.end_season<e.season))throw new Error('事件结束季不能早于开始季。');
    latest=await api('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bureaus:Number($('setup-bureaus').value),villages_per_bureau:Number($('setup-villages').value),farmers_min:Number($('setup-min').value),farmers_max:Number($('setup-max').value),seed:Number($('setup-seed').value),initial_cash_factor:Number($('setup-cash-factor').value),events})});
    bundle={run_id:latest.run_id,snapshots:[latest]};displayed=latest;external=false;
    draft=structuredClone(latest.environment);$('scenario').value='baseline';view.scope=view.selected='basin';view.diagram='topology';
    $('setup-dialog').close();render();notice('已新建运行；事件按季自动生效，可选择连续运行 10 季。旧记录仍可回放。');
  }catch(e){$('setup-error').textContent=e.message;}finally{$('new-run').disabled=false;}
};
$('logs-open').onclick=openRuns;
$('import-log').onchange=async event=>{try{const file=event.target.files[0];if(file){await replay(JSON.parse(await file.text()));$('logs-dialog').close();}}catch(e){notice(e.message,true);}finally{event.target.value='';}};
$('export-button').onclick=()=>{
  const blob=new Blob([JSON.stringify(bundle,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=`${bundle.run_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('previous-tick').onclick=()=>showTick(displayed.tick-1);
$('next-tick').onclick=()=>showTick(displayed.tick+1);
$('timeline').oninput=event=>showTick(Number(event.target.value));
$('return-live').onclick=async()=>{try{await loadLive();notice('');}catch(e){notice(e.message,true);}};
$('view-topology').onclick=()=>{view.diagram='topology';render();};
$('view-atlas').onclick=()=>{view.diagram='atlas';view.scope='basin';view.regionFilter='all';render();};
$('atlas-region').onchange=()=>{view.regionFilter=$('atlas-region').value;render();};
$('focus-village').onclick=()=>{const vid=selectedVillage();if(vid){view.scope=vid;view.selected=vid;view.diagram='parcels';render();}};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.querySelector('.workspace').requestFullscreen();}catch{notice('浏览器未允许全屏，可继续使用缩放与拖动画布。');}};
$('view-parcels').onclick=()=>{view.diagram='parcels';render();};
$('zoom-in').onclick=()=>topology.scale(1.3);
$('zoom-out').onclick=()=>topology.scale(1/1.3);
$('zoom-fit').onclick=()=>topology.fit();
document.querySelectorAll('[data-layer]').forEach(el=>el.onclick=()=>{view.layer=el.dataset.layer;if(view.layer==='quota'){view.scope='basin';view.diagram='topology';if(!displayed.bureaus[view.selected])view.selected='basin';}render();});
document.querySelectorAll('[data-chart]').forEach(el=>el.onclick=()=>{view.chart=el.dataset.chart;render();});

try{
  config=await api('/api/config');draft=structuredClone(config.presets.baseline);delete draft.name;draft.scenario='baseline';
  await loadLive();
  draft=structuredClone(latest.base_environment||latest.environment);$('scenario').value=draft.scenario;updateDraftSummary();
  if(params.get('run')&&params.get('run')!==latest.run_id){
    await replay(await api(`/api/runs/${encodeURIComponent(params.get('run'))}`));
    view.scope=params.get('scope')||'basin';view.selected=params.get('node')||view.scope;
    view.diagram=['parcels','atlas'].includes(params.get('view'))?params.get('view'):'topology';
    render();
  }
  if(params.has('round'))showTick(Number(params.get('round')));
}catch(error){notice(`工作台暂时无法载入：${error.message}。请确认后端已启动后刷新。`,true);$('tick-button').disabled=true;}

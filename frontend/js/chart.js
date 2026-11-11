const d3=window.d3;
const percent=v=>`${(v*100).toFixed(0)}%`;
const number=v=>Math.abs(v)>=10000?`${(v/10000).toFixed(1)}万`:Math.round(v).toLocaleString();
const groups={
  water:[{title:'本季用水与红线',unit:'m³',keys:['water_m3','redline_m3','counterfactual_m3'],labels:['实际用水','考核红线','同季全部沟灌']},{title:'同供水条件下的设备节水',unit:'m³',keys:['saved_m3'],labels:['田间节水估算']},{title:'按正常供水折算的用水',unit:'m³',keys:['normalized_water_m3','normal_supply_m3'],labels:['现有设备，正常供水','全部沟灌，正常供水'],normalized:true}],
  adoption:[{title:'农户承诺与安装',unit:'比例',keys:['installed_rate','commitment_rate'],labels:['已安装户数','已承诺户数'],percent:true},{title:'已安装面积占比',unit:'比例',keys:['installed_area_rate'],labels:['已运行面积'],percent:true}],
  policy:[{title:'灌溉水价',unit:'元/m³',keys:['water_price'],labels:['水价']},{title:'节水设备补贴比例',unit:'比例',keys:['subsidy_rate'],labels:['补贴比例'],percent:true}],
  credit:[{title:'贷款与逾期余额',unit:'元',keys:['loan_cny','overdue_cny'],labels:['贷款本金余额','逾期本息']},{title:'新贷款季利率',unit:'比例',keys:['bank_rate'],labels:['新贷款季利率'],percent:true}]
};

export function renderCharts(container, history, scope, group, selectedTick, onTick){
  container.replaceChildren();
  const entries=history.filter(h=>h.metrics[scope]);
  // Each saved run starts at season zero with normal supply and fixed land area.
  // Derive display-only comparisons so existing logs work without rewriting them.
  const normalSupply=entries[0]?.metrics[scope].counterfactual_m3;
  const data=entries.map(item=>{
    const m=item.metrics[scope];
    return {...item,values:{...m,normal_supply_m3:normalSupply,normalized_water_m3:m.water_m3*normalSupply/m.counterfactual_m3},
      supplyRatio:m.counterfactual_m3/normalSupply};
  });
  if(!data.length){container.textContent='当前范围尚无历史记录。';return;}
  if(group==='water'){
    const note=document.createElement('p');note.className='chart-context';
    note.textContent='浅黄色背景为干旱季；“同季全部沟灌”保留当季供水条件。正常供水折算图固定面积与设备状态，单独展示设备效果，数值不是实际取水量。';container.append(note);
  }
  for(const config of groups[group]){
    const figure=document.createElement('figure');figure.className=`chart-figure${config.normalized?' normalized-chart':''}`;
    const caption=document.createElement('figcaption');
    const name=document.createElement('span');name.textContent=config.title;
    const unit=document.createElement('span');unit.textContent=config.unit;caption.append(name,unit);figure.append(caption);
    container.append(figure);
    const w=Math.max(280,figure.clientWidth),h=176,margin={left:52,right:20,top:18,bottom:30};
    const svg=d3.select(figure).append('svg').attr('class','chart-svg').attr('viewBox',`0 0 ${w} ${h}`).attr('role','img').attr('aria-label',`${config.title}，点击季节查看快照`);
    const maxTick=Math.max(1,...data.map(d=>d.tick));
    const x=d3.scaleLinear().domain([0,maxTick]).range([margin.left,w-margin.right]);
    const maximum=Math.max(0,...data.flatMap(d=>config.keys.map(k=>d.values[k])));
    const y=d3.scaleLinear().domain([0,config.percent?1:maximum*1.15||1]).nice().range([h-margin.bottom,margin.top]);
    const fmt=config.percent?percent:config.keys[0]==='water_price'?v=>v.toFixed(2):number;
    if(group==='water'){
      const bands=[];
      for(const item of data){
        if(item.supplyRatio>=1-1e-8)continue;
        const last=bands.at(-1);
        if(last&&last.end===item.tick-1&&Math.abs(last.ratio-item.supplyRatio)<1e-8)last.end=item.tick;
        else bands.push({start:item.tick,end:item.tick,ratio:item.supplyRatio});
      }
      for(const band of bands){
        const left=x(Math.max(0,band.start-.5)),right=x(Math.min(maxTick,band.end+.5));
        svg.append('rect').attr('class','drought-band').attr('x',left).attr('y',margin.top).attr('width',right-left).attr('height',h-margin.bottom-margin.top)
          .append('title').text(`第${band.start}${band.end>band.start?'～'+band.end:''}季干旱，供水为正常的${percent(band.ratio)}`);
        if(right-left>66)svg.append('text').attr('class','drought-label').attr('x',(left+right)/2).attr('y',margin.top+13).attr('text-anchor','middle').text(`供水 ${percent(band.ratio)}`);
      }
    }
    const grid=svg.append('g').attr('class','chart-grid');
    for(const t of y.ticks(4)){
      grid.append('line').attr('x1',margin.left).attr('x2',w-margin.right).attr('y1',y(t)).attr('y2',y(t));
      grid.append('text').attr('x',margin.left-10).attr('y',y(t)+4).attr('text-anchor','end').text(fmt(t));
    }
    for(const t of x.ticks(Math.min(w<450?4:10,maxTick)).filter(Number.isInteger)) svg.append('text').attr('x',x(t)).attr('y',h-9).attr('text-anchor','middle').text(t);
    svg.append('text').attr('x',w-3).attr('y',h-9).attr('text-anchor','end').text('季');
    config.keys.forEach((key,i)=>{
      svg.append('path').datum(data).attr('class',`chart-line series-${i}`).attr('d',d3.line().x(d=>x(d.tick)).y(d=>y(d.values[key])));
      if(data.length===1)svg.append('circle').attr('class','chart-point').attr('cx',x(0)).attr('cy',y(data[0].values[key])).attr('r',3);
    });
    for(let i=1;i<data.length;i++){
      const item=data[i];
      if(item.changed || (i>1&&item.scenario!==data[i-1].scenario)){
        svg.append('line').attr('class','scenario-marker').attr('x1',x(item.tick)).attr('x2',x(item.tick)).attr('y1',margin.top).attr('y2',h-margin.bottom)
          .append('title').text(`第${item.tick}季环境切换：${item.scenario}`);
        svg.append('text').attr('x',x(item.tick)).attr('y',margin.top-5).attr('text-anchor','middle').text('换景');
      }
    }
    svg.append('line').attr('class','chart-cursor').attr('x1',x(selectedTick)).attr('x2',x(selectedTick)).attr('y1',margin.top).attr('y2',h-margin.bottom);
    const tooltip=svg.append('text').attr('x',margin.left).attr('y',12).text('');
    svg.on('pointermove',event=>{
      const [px]=d3.pointer(event,svg.node());const tick=Math.max(0,Math.min(maxTick,Math.round(x.invert(px))));
      const item=data.find(d=>d.tick===tick);if(!item)return;
      tooltip.text(`第${tick}季  ${config.keys.map((k,i)=>`${w<700?'':config.labels[i]+' '}${fmt(item.values[k])}`).join(' / ')}${group==='water'?' 供水'+percent(item.supplyRatio):''}`);
    }).on('pointerleave',()=>tooltip.text('')).on('click',event=>{
      const [px]=d3.pointer(event,svg.node());onTick(Math.max(0,Math.min(data.at(-1).tick,Math.round(x.invert(px)))));
    });
    const legend=document.createElement('div');legend.className='chart-legend';
    config.labels.forEach((label,i)=>{const span=document.createElement('span'),swatch=document.createElement('i');if(i)swatch.className=i===1?'reference':'comparison';span.append(swatch,document.createTextNode(label));legend.append(span);});
    figure.append(legend);container.append(figure);
  }
}

const d3 = window.d3;
const statusNames = {uncommitted: '未承诺', pending: '待安装', operating: '运行中'};

export class Topology {
  constructor(element, pick) {
    this.svg = d3.select(element);
    this.pick = pick;
    this.transform = d3.zoomIdentity;
    this.scope = null;
    this.zoom = d3.zoom().scaleExtent([.55, 3]).on('zoom', event => {
      this.transform = event.transform;
      this.group?.attr('transform', this.transform);
      this.group?.classed('atlas-labels-visible', this.transform.k >= 1.7);
    });
    this.svg.call(this.zoom).on('dblclick.zoom', null);
    this.resize = new ResizeObserver(() => {
      const width = element.clientWidth < 640 ? Math.max(280, element.clientWidth) : 1000;
      if (width !== this.viewportWidth) {
        this.viewportWidth = width;
        this.svg.attr('viewBox', `0 0 ${width} 440`);
        this.fit();
      }
    });
    this.resize.observe(element.parentElement);
  }

  scale(factor) { this.svg.call(this.zoom.scaleBy, factor); }
  fit() { this.svg.call(this.zoom.transform, this.viewportWidth < 640 ? d3.zoomIdentity.translate(this.viewportWidth/2-500,0) : d3.zoomIdentity); }
  activity(ids) { this.svg.selectAll('.node').classed('active', d => ids.includes(d.id)); }

  render(state, view) {
    const priorFocus = document.activeElement?.getAttribute('data-entity');
    this.state = state;
    this.view = view;
    this.svg.selectAll('*').remove();
    const defs = this.svg.append('defs');
    defs.append('marker').attr('id', 'quota-arrow').attr('viewBox', '0 -5 10 10').attr('refX', 9)
      .attr('markerWidth', 7).attr('markerHeight', 7).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L9,0L0,4').attr('class', 'quota-arrow-head');
    this.group = this.svg.append('g').attr('transform', this.transform).classed('atlas-labels-visible',this.transform.k>=1.7);
    this.positions = new Map();
    if (this.scope !== view.scope + ':' + view.diagram + ':' + view.regionFilter) {
      this.scope = view.scope + ':' + view.diagram + ':' + view.regionFilter;
      this.fit();
    }
    if(view.diagram==='atlas')this.drawAtlas();
    else if (state.villages[view.scope]) {
      if (view.diagram === 'parcels') this.drawParcels(state.villages[view.scope]);
      else this.drawVillage(state.villages[view.scope]);
    } else this.drawOverview();
    if (view.layer === 'credit'&&view.diagram!=='atlas') this.drawCredit();
    if (view.layer === 'quota'&&view.diagram!=='atlas') this.drawQuota();
    if (priorFocus) this.svg.selectAll('[data-entity]').filter(function () { return this.getAttribute('data-entity') === priorFocus; }).node()?.focus({preventScroll: true});
  }

  link(a, b, className = 'management-link') {
    return this.group.append('path').attr('class', className)
      .attr('d', `M${a.x},${a.y}C${a.x},${(a.y+b.y)/2} ${b.x},${(a.y+b.y)/2} ${b.x},${b.y}`);
  }

  node(id, x, y, options = {}) {
    const {state, view} = this;
    const f = state.farmers[id], bank = state.banks[id], bureau = state.bureaus[id], village = state.villages[id];
    const entity = f || bank || bureau || village || state.basin;
    const width = options.width || (f ? 80 : 156);
    const height = f ? 44 : 56;
    const pos = {x, y};
    this.positions.set(id, pos);
    const g = this.group.append('g').datum({id}).attr('class', `node ${f?.status || ''} ${view.selected === id ? 'selected' : ''}`)
      .attr('transform', `translate(${x},${y})`).attr('role', 'button').attr('tabindex', 0)
      .attr('data-entity', id).attr('aria-label', `${entity.name}，${f ? statusNames[f.status] : bank ? '查看授信与利率' : '查看详情并聚焦'}`)
      .on('click', event => {event.stopPropagation(); this.pick(id);})
      .on('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); this.pick(id);}});
    g.append('rect').attr('class', 'node-hit').attr('x', -width/2-4).attr('y', -height/2-4).attr('width', width+8).attr('height', height+8);
    if (f) {
      const radius = Math.max(9, Math.min(19, Math.sqrt(f.area_mu) * 2.5));
      g.append('circle').attr('class', 'node-surface').attr('r', radius).attr('cy', -6);
      g.append('text').attr('text-anchor', 'middle').attr('y', -2).attr('class', 'farmer-number').text(id.split('-F')[1]);
      g.append('text').attr('text-anchor', 'middle').attr('y', 29).attr('class', 'node-caption').text(`${f.area_mu.toFixed(1)}亩`);
      g.append('text').attr('text-anchor', 'middle').attr('y', 44).attr('class', 'node-tag').text(
        f.status === 'pending' ? '待安装' : f.status === 'operating' ? '运行中' : f.financing?.approved === false ? '拒贷' : f.decision?.source === 'rule' ? '规则' : '未承诺');
    } else if (bank) {
      g.append('path').attr('class', 'node-surface').attr('d', `M${-width/2},0L${-width/2+12},-26H${width/2-12}L${width/2},0L${width/2-12},26H${-width/2+12}Z`);
      g.append('text').attr('text-anchor', 'middle').attr('y', -2).text(options.short ? width<50?'银行':'农商行' : '村农商行');
      g.append('text').attr('text-anchor', 'middle').attr('y', 16).attr('class', 'node-caption').text(`${(bank.rate*100).toFixed(width<50?0:1)}%${width>80?' 季利率':''}`);
    } else {
      g.append('rect').attr('class', 'node-surface').attr('x', -width/2).attr('y', -height/2).attr('width', width).attr('height', height).attr('rx', bureau ? 6 : 2);
      g.append('text').attr('text-anchor', 'middle').attr('y', -3).text(options.label || entity.name);
      const metric = state.metrics[id];
      const caption = bureau ? `第${bureau.rank}名 / ${(bureau.subsidy_rate*100).toFixed(0)}%补贴` : village ? width<60?`${metric.installed_count}/${metric.farmer_count}`:width<100?`${metric.installed_count}/${metric.farmer_count}户`:`${metric.installed_count} / ${metric.farmer_count}户运行` : '由你下达指示';
      g.append('text').attr('text-anchor', 'middle').attr('y', 17).attr('class', 'node-caption').text(caption);
    }
    g.append('title').text(`${entity.name}\n${id}${f ? `\n${statusNames[f.status]}，${f.area_mu.toFixed(1)}亩` : ''}`);
    return g;
  }

  drawOverview() {
    const {state, view} = this;
    const regions = state.bureaus[view.scope] ? [state.bureaus[view.scope]] : Object.values(state.bureaus);
    const hierarchy = d3.hierarchy({id: 'basin', children: regions.map(b => ({id: b.id, children: b.villages.map(id => ({id}))}))});
    d3.tree().size([900, 222]).separation((a,b) => a.parent === b.parent ? 1 : 1.45)(hierarchy);
    for (const n of hierarchy.descendants()) {n.x += 50; n.y += 47;}
    for (const l of hierarchy.links()) this.link(l.source, l.target);
    const dense = hierarchy.leaves().length > 9;
    for (const n of hierarchy.descendants()) {
      const v = state.villages[n.data.id];
      const width = n.depth === 2 ? Math.max(28, Math.min(112, 820 / hierarchy.leaves().length - 16)) : n.depth===1?Math.min(156,900/regions.length-20):156;
      this.node(n.data.id, n.x, n.y, {width, label: v && dense ? v.name.slice(2) : undefined});
      if (v) {
        const bankPos = {x:n.x, y:363};
        this.link({x:n.x,y:n.y+28}, bankPos);
        this.node(v.bank_id, bankPos.x, bankPos.y, {width: width, short:true});
      }
    }
  }

  drawVillage(village) {
    const b = this.state.bureaus[village.bureau_id];
    this.node(b.id, 310, 42, {width:166});
    this.node(village.id, 530, 42, {width:166});
    this.link({x:393,y:42}, {x:447,y:42});
    this.node(village.bank_id, 120, 195, {width:146});
    const cols = 6, rows = Math.ceil(village.farmers.length/cols);
    for (let i=0;i<village.farmers.length;i++) {
      const x = 310 + (i%cols)*117;
      const y = 145 + Math.floor(i/cols)*104;
      if (this.view.layer === 'management') this.link({x:530,y:72}, {x,y:y-24});
      this.node(village.farmers[i], x,y);
    }
    if (rows <= 3) this.group.append('text').attr('x', 120).attr('y', 260).attr('class','map-label-small').attr('text-anchor','middle').text('圆的大小对应经营面积');
    this.drawNeighbors();
  }

  drawParcels(village) {
    const fc = {type:'FeatureCollection', features:[{type:'Feature',geometry:village.boundary}]};
    const projection = d3.geoIdentity().reflectY(true).fitExtent([[245,28],[940,406]],fc);
    const path = d3.geoPath(projection);
    this.node(village.bank_id, 110, 100, {width:155});
    this.node(village.bureau_id, 110, 220, {width:155});
    for (const feature of village.parcels) {
      const id = feature.properties.farmer_id, f=this.state.farmers[id];
      const [x,y]=path.centroid(feature);
      this.positions.set(id,{x,y});
      this.group.append('path').datum({id}).attr('d', path(feature)).attr('class',`parcel ${f.status} ${this.view.selected===id?'selected':''}`)
        .attr('role','button').attr('tabindex',0).attr('data-entity',id).attr('aria-label',`${f.name}，${f.area_mu.toFixed(1)}亩，${statusNames[f.status]}`)
        .on('click',()=>this.pick(id)).on('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();this.pick(id);}})
        .append('title').text(`${f.name}，${f.area_mu.toFixed(1)}亩，${statusNames[f.status]}`);
      const label=this.group.append('text').attr('class','map-label').attr('x',x).attr('y',y-2);
      label.append('tspan').attr('x',x).text(id.split('-F')[1]);
      label.append('tspan').attr('x',x).attr('dy',15).attr('class','map-label-small').text(`${f.area_mu.toFixed(1)}亩`);
    }
    this.group.append('path').attr('d',path(fc.features[0])).attr('class','map-boundary').attr('pointer-events','none');
    this.drawNeighbors();
  }

  drawAtlas(){
    const {state,view}=this;
    const regions=Object.values(state.bureaus).filter(b=>view.regionFilter==='all'||view.regionFilter===b.id);
    const villages=regions.flatMap(b=>b.villages.map(id=>state.villages[id]));
    const columns=Math.ceil(villages.length/2),cellWidth=900/columns;
    const riverPoints=Array.from({length:31},(_,i)=>[35+i*31,220+17*Math.sin(i/4)]);
    const river=d3.line().curve(d3.curveBasis)(riverPoints);
    this.group.append('path').attr('class','atlas-river-bank').attr('d',river);
    this.group.append('path').attr('class','atlas-river').attr('d',river);
    this.group.append('text').attr('class','river-label').attr('x',45).attr('y',202).text('上游');
    this.group.append('text').attr('class','river-label').attr('x',910).attr('y',202).text('下游');
    this.group.append('text').attr('class','river-caption').attr('x',500).attr('y',261).text('虚构河流，村庄沿两岸排列');
    villages.forEach((v,i)=>{
      const bureau=state.bureaus[v.bureau_id],m=state.metrics[v.id];
      const col=Math.floor(i/2),side=i%2,x=50+col*cellWidth,top=side?286:12;
      const header=this.group.append('g').attr('class','atlas-header').attr('role','button').attr('tabindex',0)
        .attr('aria-label',`查看${v.name}详情`).on('click',()=>this.pick(v.id))
        .on('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();this.pick(v.id);}});
      header.append('rect').attr('class','node-hit').attr('x',x).attr('y',top).attr('width',cellWidth-6).attr('height',38);
      header.append('text').attr('class','atlas-village-label').attr('x',x+cellWidth/2).attr('y',top+12).text(v.name);
      header.append('text').attr('class','atlas-stat-label').attr('x',x+cellWidth/2).attr('y',top+29).text(`${m.farmer_count}户 / ${m.installed_count}户运行`);
      const fc={type:'FeatureCollection',features:[{type:'Feature',geometry:v.boundary}]};
      const projection=d3.geoIdentity().reflectY(true).fitExtent([[x+8,top+40],[x+cellWidth-8,top+138]],fc);
      const path=d3.geoPath(projection);
      for(const feature of v.parcels){
        const id=feature.properties.farmer_id,f=state.farmers[id],[cx,cy]=path.centroid(feature);
        this.positions.set(id,{x:cx,y:cy});
        this.group.append('path').datum({id}).attr('d',path(feature)).attr('class',`parcel atlas-parcel ${f.status} ${view.selected===id?'selected':''}`)
          .attr('role','button').attr('tabindex',0).attr('data-entity',id).attr('aria-label',`${v.name}${f.name}，${statusNames[f.status]}`)
          .on('click',()=>this.pick(id)).on('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();this.pick(id);}})
          .append('title').text(`${bureau.name} / ${v.name} / ${f.name}\n${f.area_mu.toFixed(1)}亩，${statusNames[f.status]}\n本季用水 ${Math.round(f.water_m3).toLocaleString()} m³`);
        this.group.append('text').attr('class','atlas-farmer-label').attr('x',cx).attr('y',cy+3).text(id.split('-F')[1]);
      }
      this.group.append('path').attr('d',path(fc.features[0])).attr('class','map-boundary').attr('pointer-events','none');
    });
  }

  drawNeighbors() {
    const farmer=this.state.farmers[this.view.selected];
    if(!farmer || this.view.layer!=='management') return;
    const from=this.positions.get(farmer.id);
    if(!from) return;
    for(const id of farmer.neighbors){const to=this.positions.get(id);if(to)this.group.append('line').attr('class','neighbor-link').attr('x1',from.x).attr('y1',from.y).attr('x2',to.x).attr('y2',to.y);}
  }

  drawCredit() {
    const {state,view}=this;
    const village=state.villages[view.scope];
    if(!village) return;
    const bank=state.banks[village.bank_id], start=this.positions.get(bank.id);
    const decisions=new Map(bank.approvals.map(a=>[a.farmer_id,a]));
    for(const fid of village.farmers){
      const f=state.farmers[fid];
      if(f.loan?.principal_cny>0 && !decisions.has(fid)) decisions.set(fid,{farmer_id:fid,approve:true,amount_cny:f.loan.principal_cny,used:true});
    }
    for(const [fid,a] of decisions){
      if(state.farmers[view.selected] && view.selected!==fid) continue;
      const to=this.positions.get(fid);if(!start||!to)continue;
      const edge=this.link(start,to,`credit-link ${a.approve?'approved':'rejected'}`);
      edge.append('title').text(`${state.farmers[fid].name}：${a.approve?`获批 ${a.amount_cny.toFixed(0)}元${a.used?'，已使用':'，未使用'}`:'拒贷'}`);
    }
    this.group.selectAll('.node').raise();
  }

  drawQuota() {
    for(const t of this.state.quota_transfers){
      const from=this.positions.get(t.from), to=this.positions.get(t.to);if(!from||!to)continue;
      const y1=from.y-28, y2=to.y-28, mid=(from.x+to.x)/2;
      const d=`M${from.x},${y1}Q${mid},${Math.min(y1,y2)-95} ${to.x},${y2}`;
      const edge=this.group.append('path').attr('d',d).attr('class','quota-link').attr('marker-end','url(#quota-arrow)')
        .attr('tabindex',0).attr('role','button').attr('aria-label',`配额互助 ${t.volume_m3.toFixed(0)}立方米`)
        .on('click',()=>this.pick(`transfer:${t.id}`)).on('keydown',e=>{if(e.key==='Enter')this.pick(`transfer:${t.id}`);});
      edge.append('title').text(`${this.state.bureaus[t.from].name}转给${this.state.bureaus[t.to].name}：${t.volume_m3.toFixed(0)} m³`);
      this.group.append('text').attr('x',mid).attr('y',y1-42).attr('text-anchor','middle').attr('class','quota-label')
        .text(`${Math.round(t.volume_m3).toLocaleString()} m³`).on('click',()=>this.pick(`transfer:${t.id}`));
    }
  }
}

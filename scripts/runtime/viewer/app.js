(function(){
'use strict';
const E=RevisionEngine,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={correspondence:'对应',split:'拆分',merge:'合并',reorganization:'重组',addition:'新增',deletion:'删除',reviewed:'已核对',needs_review:'待复核',unreviewed:'尚未核对',unknown:'影响待判断',judgement:'判断',evidence:'证据',wording:'措辞',structure:'结构',human_expert:'人工专家',cited_expert:'引用专家',ai_review:'AI审阅',open:'未决',accepted:'已采纳',partially_accepted:'部分采纳',declined:'未采纳',moot:'已不适用',source_baseline:'与输入新版一致',unsynced:'审阅修订版，尚未同步正式报告'};
const L=x=>labels[x]??x,button=(action,text,extra='')=>`<button type="button" data-action="${action}" ${extra}>${esc(text)}</button>`;
const chip=x=>`<span class="badge ${x==='needs_review'?'warning':''}">${esc(L(x))}</span>`;
let data=JSON.parse(document.getElementById('revision-package').textContent),base=E.clone(data),active=null,filter='changed',navMode='changes',query='',navClosed=false,dockClosed=false,dirty=false,busy=false,printSummary=false,showDetails=false;
const undo=[],redo=[],positions=[],cache=new Map();let composing=false;
const scrollSkips=new WeakMap();let scrollLink=null;
function setPaneTop(pane,top){scrollSkips.set(pane,Math.max(0,Math.min(top,pane.scrollHeight-pane.clientHeight)));pane.scrollTop=top;}
function anchorScroll(){const panes=Array.from(app.querySelectorAll('.pane-scroll'));scrollLink={mode:viewMode};for(const p of panes)scrollLink[p.dataset.role]=p.scrollTop;}
function syncPaneScroll(source){
 const other=app.querySelector(`.pane-scroll[data-role="${source.dataset.role==='old'?'new':'old'}"]`);if(!other)return;
 const expected=scrollSkips.get(source);
 if(expected!==undefined){scrollSkips.delete(source);if(Math.abs(source.scrollTop-expected)<2)return;}
 // Move both panes by the same reading distance, irrespective of document length.
 // Re-anchor after clamping so reversing at either end responds immediately.
 if(scrollLink?.mode===viewMode){
  const delta=source.scrollTop-scrollLink[source.dataset.role];
  setPaneTop(other,scrollLink[other.dataset.role]+delta);
 }
 anchorScroll();
}

let viewMode=data.page_previews?'pages':'text';
const app=document.getElementById('app'),modal=document.getElementById('modal');
const blocks=()=>new Map(data.blocks.map(b=>[b.id,b]));
const related=id=>data.relations.find(r=>r.id===id);
const blockRelation=id=>data.relations.find(r=>r.from_ids.includes(id)||r.to_ids.includes(id));
const blockText=b=>E.text(data,b);
const ownChanges=r=>data.changes.filter(c=>c.relation_id===r?.id);
const isImportant=r=>ownChanges(r).some(c=>c.impact.labels.some(x=>['judgement','evidence','unknown'].includes(x)));
function blockLabel(id){const b=blocks().get(id);if(!b)return '未定位内容';const doc=data.source_documents.find(d=>d.id===b.document_id);return `${doc.role==='old'?'旧稿':'新稿'}第 ${b.order+1} 段`;}
const isChanged=r=>r.content_changed||r.moved||r.kind!=='correspondence';
// Only short, explicitly labelled front-matter metadata is considered detail.
// Titles, scope, evidence dates in prose, numeric results and conclusions stay visible.
function isDetailRevision(r){return [...r.from_ids,...r.to_ids].every(id=>{const b=blocks().get(id);return b?.content.type==='text'&&b.order<30&&blockText(b).length<240&&/^(编制日期|修订日期|发布日期|报告日期|资料截止日期|版本|版次|报告名称|报告类型|评价类型|编制单位|编制人|审核人|审定人|总页数)(?:[：:\s]|$)/u.test(blockText(b).trim());});}
const visibleChange=r=>isChanged(r)&&(showDetails||!isDetailRevision(r));
function relationTitle(r){if(!r)return '未选择';const first=blocks().get(r.to_ids[0]??r.from_ids[0]);const body=(first?.content.type==='text'?blockText(first):first?.kind==='table'?'表格':'图件')??'';const n=data.relations.indexOf(r)+1;return `${r.content_changed||r.moved?'修改':'对应'} ${String(n).padStart(2,'0')} · ${body.replace(/\s+/g,' ').slice(0,35)}`;}
function pageRevisionMarks(doc){
 const marks=new Map(),preview=data.page_previews[doc.id];
 for(const b of data.blocks.filter(b=>b.document_id===doc.id&&b.content.type==='text')){
  const r=blockRelation(b.id);if(!r||!visibleChange(r)||r.to_ids.some(id=>data.working_copy.overrides[id]))continue;
  const glyphs=preview.glyphs?.[b.id],segments=segmentsFor(r,b,diffFor(r));if(!glyphs||!segments)continue;
  let offset=0;const changed=[];for(const segment of segments){if(segment.changed)changed.push([offset,offset+segment.text.length]);offset+=segment.text.length;}
  const selected=glyphs.filter(g=>changed.some(([lo,hi])=>g.start<hi&&g.end>lo));let line=null;
  for(const g of selected){const box=g.box;
   if(line&&line.page===box.page&&Math.abs(line.y-box.y)<Math.max(10,box.h*.12)&&box.x>=line.x&&box.x<=line.x+line.w+box.w*.4){line.w=Math.max(line.w,box.x+box.w-line.x);line.h=Math.max(line.h,box.h);}
   else {line={...box,bid:b.id};if(!marks.has(box.page))marks.set(box.page,[]);marks.get(box.page).push(line);}
  }
 }
 return marks;
}
function pagePane(doc){
 const preview=data.page_previews[doc.id];if(!preview)return '<p>本版没有页面预览，请切换文字审阅。</p>';
 const revisions=pageRevisionMarks(doc);
 const owned=new Map();for(const [bid,location]of Object.entries(preview.locations)){
  const groups=new Map();for(const box of location.boxes){const prev=groups.get(box.page);if(!prev)groups.set(box.page,{...box});else{const right=Math.max(prev.x+prev.w,box.x+box.w),bottom=Math.max(prev.y+prev.h,box.y+box.h);prev.x=Math.min(prev.x,box.x);prev.y=Math.min(prev.y,box.y);prev.w=right-prev.x;prev.h=bottom-prev.y;}}
  let first=true;for(const box of groups.values()){if(!owned.has(box.page))owned.set(box.page,[]);owned.get(box.page).push({bid,box,first,method:location.method});first=false;}
 }
 return preview.pages.map(p=>`<section class="pdf-page" data-page="${p.number}"><div class="page-number">第 ${p.number} / ${preview.pages.length} 页</div><div class="page-paper" style="aspect-ratio:${p.width}/${p.height}"><img class="page-image" src="${esc(p.data_url)}" alt="${doc.role==='old'?'旧稿':'新稿'}原PDF第${p.number}页" loading="lazy">${(owned.get(p.number)??[]).map(({bid,box,first,method})=>{const r=blockRelation(bid);return `<button class="page-locator ${r?.id===active?'active':''} ${r&&visibleChange(r)?'changed':''} ${method==='partial_anchor'?'partial':''}" ${first?`id="block-${esc(bid)}"`:''} data-action="pdf-block" data-id="${esc(bid)}" aria-label="定位${esc(blockLabel(bid))}" title="${esc(blockLabel(bid))}${method==='partial_anchor'?'（部分文字锚点）':''}" style="left:${box.x/1000}%;top:${box.y/1000}%;width:${box.w/1000}%;height:${box.h/1000}%"></button>`;}).join('')}${(revisions.get(p.number)??[]).map(box=>`<span class="pdf-revision ${doc.role==='old'?'removed':'added'}" data-revision-block="${esc(box.bid)}" aria-hidden="true" style="left:${box.x/1000}%;top:${(box.y+box.h*(doc.role==='old'?.52:.94))/1000}%;width:${box.w/1000}%"></span>`).join('')}</div></section>`).join('');
}

function notify(text){const s=document.getElementById('status');if(s)s.textContent=text;}
function scrollState(){return Array.from(app.querySelectorAll('.pane-scroll')).map(p=>({role:p.dataset.role,top:p.scrollTop}));}
function restoreScroll(state){for(const s of state){const p=app.querySelector(`.pane-scroll[data-role="${s.role}"]`);if(p)setPaneTop(p,s.top);}}
function content(b,segments){
 const c=b.content;if(c.type==='text')return segments?segments.map(s=>s.changed?`<${data.source_documents.find(d=>d.id===b.document_id).role==='old'?'del':'ins'}>${esc(s.text)}</${data.source_documents.find(d=>d.id===b.document_id).role==='old'?'del':'ins'}>`:esc(s.text)).join(''):esc(blockText(b));
 if(c.type==='table')return '<table><thead><tr>'+c.headers.map(x=>`<th>${esc(x)}</th>`).join('')+'</tr></thead><tbody>'+c.rows.map(row=>'<tr>'+row.map(x=>`<td>${esc(x)}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
 return `<figure><img src="${esc(c.data_url)}" alt="${esc(c.alt)}" data-image="${esc(b.id)}"><figcaption>${esc(c.caption)}</figcaption></figure>`;
}
function diffFor(r){
 if(!r)return null;const bs=blocks(),members=[...r.from_ids,...r.to_ids];
 if(!members.every(id=>bs.get(id)?.content.type==='text'))return {status:'unsupported'};
 const a=r.from_ids.map(id=>blockText(bs.get(id))).join('\n'),b=r.to_ids.map(id=>blockText(bs.get(id))).join('\n');
 const key=E.canonical([r.id,a,b]);if(cache.has(key))return cache.get(key);
 let value;try{value=E.diff(a,b);}catch(_){value={status:'stale'};}cache.set(key,value);return value;
}
function segmentsFor(r,b,diff){
 if(!diff||diff.status!=='current')return null;
 const role=data.source_documents.find(d=>d.id===b.document_id).role,ids=role==='old'?r.from_ids:r.to_ids,bs=blocks();
 let offset=0;for(const id of ids){if(id===b.id)break;offset+=blockText(bs.get(id)).length+1;}
 const end=offset+blockText(b).length;let pos=0;const seg=[];
 for(const s of diff[role]){const lo=Math.max(offset,pos),hi=Math.min(end,pos+s.text.length);if(hi>lo)seg.push({text:s.text.slice(lo-pos,hi-pos),changed:s.changed});pos+=s.text.length;}
 return seg;
}
function blockHtml(b){
 const r=blockRelation(b.id),selected=r?.id===active,diff=r&&visibleChange(r)?diffFor(r):null;
 return `<article class="block ${b.kind==='heading'?'heading':''} ${selected?'active':''} ${r&&visibleChange(r)?'changed':''} ${!r?'unmatched':''}" data-block-id="${esc(b.id)}" id="block-${esc(b.id)}" tabindex="0"><div class="block-tools"><small>${esc(blockLabel(b.id))}</small>${!r?chip('未配对'):''}${r?.moved?chip('移动'):''}${b.editable?button('edit-block','编辑',`data-id="${esc(b.id)}"`):''}</div><div class="block-text">${content(b,segmentsFor(r,b,diff))}</div></article>`;
}
function navEntries(){
 if(navMode==='chapters')return data.blocks.filter(b=>b.kind==='heading'&&(blockText(b)+' '+b.id).toLowerCase().includes(query.toLowerCase())).map(b=>button('block',`${data.source_documents.find(d=>d.id===b.document_id).role==='old'?'旧':'新'} · ${blockText(b)}`,`class="nav-entry" data-id="${esc(b.id)}"`)).join('')||'<p class="muted">没有符合条件的章节</p>';

 const text=r=>[r.id,...r.from_ids,...r.to_ids].join(' ')+' '+[...r.from_ids,...r.to_ids].map(id=>{const b=blocks().get(id);return b?.content.type==='text'?blockText(b):JSON.stringify(b?.content.type==='table'?b.content.rows:b?.content.caption??'');}).join(' ')+' '+data.reasons.filter(x=>x.relation_ids.includes(r.id)).map(x=>x.text).join(' ');
 const relations=data.relations.filter(r=>(showDetails||!isDetailRevision(r))&&(filter!=='important'||isImportant(r))&&(filter!=='review'||r.review_status!=='reviewed'||r.confidence<70)&&(navMode!=='changes'||isChanged(r))&&(filter!=='changed'||isChanged(r))&&(filter!=='unmatched')&&text(r).toLowerCase().includes(query.toLowerCase()));
 let out=relations.map(r=>{const first=blocks().get(r.to_ids[0]??r.from_ids[0]);return `<button class="nav-entry" aria-current="${r.id===active}" data-action="activate" data-id="${esc(r.id)}"><b>${esc(relationTitle(r))}</b><small>${esc(first?.content.text??first?.kind??'')}</small>${r.moved?chip('移动'):''}${isImportant(r)?chip('重点'):''}${r.review_status==='needs_review'?chip('needs_review'):''}${r.confidence<70?chip('低置信度'):''}</button>`;}).join('');
 if(navMode!=='changes'&&(filter==='unmatched'||filter==='all')){
  out+=data.blocks.filter(b=>!blockRelation(b.id)&&(blockText(b)+' '+b.id).toLowerCase().includes(query.toLowerCase())).map(b=>`<button class="nav-entry" data-action="block" data-id="${esc(b.id)}">未配对 · ${esc(blockLabel(b.id))}<small>${esc(blockText(b)||b.kind)}</small></button>`).join('');
 }
 if(!out)out='<p class="muted" style="padding:12px">没有符合条件的条目</p>';
 return out;
}
function opinionHtml(o){return `<div class="detail"><h3>${esc(L(o.source_type))}</h3><p>${esc(o.quote)}</p>${chip(o.disposition)}${o.review_status==='needs_review'?chip(o.review_status):''}<details><summary>来源与处理记录</summary><small>${esc(o.source)} · ${esc(o.date??'日期未知')}</small><p>原目标：${esc(o.target.report_version)} / 审阅 ${o.target.review_revision??'源报告'}</p><p>剩余事项：${esc(o.outstanding||'未登记')}</p>${o.history.map(h=>`<p>${esc(h.at??'日期未知')} · ${esc(h.actor)} · ${esc(L(h.disposition))}<br>${esc(h.reason)}</p>`).join('')}<div class="actions">${o.target.block_ids.map(id=>button('block',blockLabel(id),`data-id="${esc(id)}"`)).join('')}</div></details></div>`;}
function dockHtml(){
 const r=related(active);if(!r)return '<p class="muted">选择一处修改，查看原因与意见。</p>';
 const cs=ownChanges(r),reasons=data.reasons.filter(x=>x.relation_ids.includes(r.id));
 const opids=new Set(cs.flatMap(c=>c.opinion_ids)),bids=new Set([...r.from_ids,...r.to_ids]);
 const opinions=data.opinions.filter(o=>opids.has(o.id)||o.target.block_ids.some(b=>bids.has(b)));
 const impacts=cs.map(c=>`<div class="detail"><h3>判断影响</h3>${c.impact.labels.map(chip).join('')}${c.impact.review_status==='needs_review'?chip('needs_review'):''}<details><summary>标注依据与核对</summary><p>${esc(c.impact.source)}</p>${chip(c.impact.review_status)}${button('confirm-impact','本人已核对当前影响',`data-id="${esc(c.id)}"`)}</details></div>`).join('');
 const diff=viewMode==='text'?diffFor(r):null;
 let output=reasons.map(x=>`<div class="detail"><h3>为什么修改 ${x.review_status==='needs_review'?chip('needs_review'):''}</h3><p data-action="activate" data-id="${esc(r.id)}" tabindex="0" title="点击定位两版内容">${esc(x.text)}</p><details><summary>依据与说明操作</summary><p>${esc(x.evidence)}</p><small>${esc(x.source)}</small>${chip(x.review_status)}<div class="actions">${button('edit-reason','编辑原因',`data-id="${esc(x.id)}"`)}${button('activate','定位相关内容',`data-id="${esc(r.id)}"`)}${button('confirm-reason','本人已核对当前原因',`data-id="${esc(x.id)}"`)}</div></details></div>`).join('')+impacts;
 if(!output)output='<p class="muted">本项尚未登记原因或专业影响。</p>';
 if(opinions.length)output+=`<details class="detail full"><summary>审阅意见 ${opinions.length} 条 · 未决 ${opinions.filter(o=>['open','partially_accepted'].includes(o.disposition)).length} 条</summary>${opinions.map(opinionHtml).join('')}</details>`;
 output+=data.decisions.filter(d=>d.change_ids.some(x=>cs.some(c=>c.id===x))).map(d=>`<div class="detail"><h3>待决定</h3><p>${esc(d.question)}</p><details><summary>依据与责任</summary><p>${esc(d.basis)}</p><small>${esc(d.owner??'未指定责任人')}</small></details></div>`).join('');
 if(viewMode==='pages'){
  const textIds=[...r.from_ids,...r.to_ids].filter(id=>blocks().get(id).content.type==='text');
  if(isChanged(r)&&textIds.some(id=>!data.page_previews[blocks().get(id).document_id]?.glyphs?.[id]))output+='<p class="muted">本项部分原页文字缺少可靠逐字坐标，请切换文字审阅查看完整修订。</p>';
  const missing=[...r.from_ids,...r.to_ids].filter(id=>!data.page_previews[blocks().get(id).document_id]?.locations[id]);
  if(missing.length)output+=`<p class="muted">本项有 ${missing.length} 个内容块未能可靠对准PDF位置，请在文字审阅中核对。</p>`;
  const b=r.to_ids.map(id=>blocks().get(id)).find(b=>b.editable);if(b)output+=`<div class="actions full">${button('edit-block','编辑本项审阅文字',`data-id="${esc(b.id)}"`)}</div>`;
 }
 output+=`<details class="detail full technical"><summary>对应与技术详情</summary><p>${esc(L(r.kind))}${r.moved?' · 移动':''} · ${esc(r.method)} / ${r.confidence}% ${chip(r.review_status)}</p><p>${esc(r.id)}</p><p>${viewMode==='pages'?'原页色框仅为定位；文字差异请看文字审阅。':diff?.status==='current'?'文字差异已计算，仅作定位。':'本组差异暂不支持或待重算，完整内容保留。'}</p><div class="actions">${[...r.from_ids,...r.to_ids].map(id=>button('member',blockLabel(id),`data-id="${esc(id)}"`)).join('')}</div>${data.relation_history.filter(h=>h.replacement_ids.includes(r.id)).map(h=>`<p>原关系 ${esc(h.retired_ids.join('、'))}；原因与意见原锚点保留，待复核。</p>`).join('')}</details>`;
 return output;
}

function render(){
 const scroll=scrollState(),navScroll=app.querySelector('.nav-list')?.scrollTop??0;
 app.innerHTML=`<header class="bar"><div><h1>${esc(data.title)}</h1><div class="document-state">${dirty?'<span class="badge warning" id="save-state">有未导出修改</span>':''}${Object.keys(data.working_copy.overrides).length?'<span class="badge warning">审阅文字已改，PDF原页未重建</span>':''}</div></div><div class="actions toolbar">${data.page_previews?`<span class="view-switch">${button('view-text','文字审阅',`aria-pressed="${viewMode==='text'}"`)}</span>`:''}${button('back','返回上一位置',positions.length?'':'disabled')}${button('next-change','显示下一处修改',data.relations.some(visibleChange)?'':'disabled')}${button('fullscreen',document.fullscreenElement?'退出全屏':'全屏显示',`aria-pressed="${!!document.fullscreenElement}"`)}<details class="experiment-menu"><summary>实验菜单</summary><div class="experiment-options">${button('nav','导航')}${button('undo','撤销',undo.length?'':'disabled')}${button('redo','重做',redo.length?'':'disabled')}${button('remap','调整对应')}${button('sources','来源/覆盖')}${button('print','打印')}${button('patch','导出补丁')}${button('export','导出审阅版 HTML','class="primary"')}</div></details></div></header><div class="work ${navClosed?'collapsed':''}"><nav aria-label="修订导航"><div class="filters">${button('nav-mode',navMode==='changes'?'切换为章节导航':'返回修改列表',`aria-pressed="${navMode==='chapters'}"`)}<input id="search" type="search" placeholder="搜索全文 / 原因 / 编号" aria-label="搜索" value="${esc(query)}">${navMode==='changes'?`<select id="filter" aria-label="筛选">${[['changed','全部修改'],['important','重点判断/证据'],['review','待核对/低置信度']].map(([v,t])=>`<option value="${v}" ${filter===v?'selected':''}>${t}</option>`).join('')}</select>`:''}<label class="detail-setting"><input type="checkbox" id="show-detail-revisions" ${showDetails?'checked':''}> 显示细节修订</label><small>${navMode==='changes'?data.relations.filter(visibleChange).length+' 处修改':'章节导航 · 旧稿 / 新稿'}</small></div><div class="nav-list">${navEntries()}</div></nav><div class="reading"><div class="panes">${['old','new'].map(role=>{const doc=data.source_documents.find(d=>d.role===role);return `<section class="doc-pane"><div class="pane-head"><b>${role==='old'?'旧稿':'新稿'}</b> · ${esc(doc.report_version)} <small>联动滚动</small></div><div class="pane-scroll" data-role="${role}" aria-label="${role==='old'?'旧稿':'新稿'}正文">${viewMode==='pages'?pagePane(doc):data.blocks.filter(b=>b.document_id===doc.id).sort((a,b)=>a.order-b.order).map(blockHtml).join('')}</div></section>`;}).join('')}</div><section class="dock ${dockClosed?'closed':''}" aria-label="当前修改说明"><div class="dock-head"><h2>${esc(relationTitle(related(active)))}</h2>${button('dock',dockClosed?'展开说明':'收起说明')}</div><div class="dock-content" ${dockClosed?'hidden':''}>${dockHtml()}</div></section></div></div><footer class="status" id="status" role="status" aria-live="polite">离线审阅 · 编辑后请导出保存</footer>`;
 restoreScroll(scroll);anchorScroll();const list=app.querySelector('.nav-list');if(list)list.scrollTop=navScroll;syncNavigation();
}
function selectionBusy(){return composing||!window.getSelection()?.isCollapsed||!!document.activeElement?.closest('textarea,input,[contenteditable=true]');}
function scrollBlock(id,pageOffset=0){let el=document.getElementById('block-'+id);if(!el){if(viewMode==='pages')notify('本块未能可靠对准PDF位置，可切换文字审阅核对。');return;}const pane=el.closest('.pane-scroll');if(viewMode==='pages')el=el.closest('.pdf-page');if(innerWidth<=720)el.scrollIntoView({block:'start',behavior:'instant'});else setPaneTop(pane,Math.max(0,pane.scrollTop+el.getBoundingClientRect().top-pane.getBoundingClientRect().top-(viewMode==='pages'?pageOffset:12)));}
// Keep corresponding pages aligned, then shift BOTH pages only as far as needed
// to expose the selected content. A paragraph taller than the viewport starts near its top.
function revealRelationPages(r){
 const targets=[r.from_ids[0],r.to_ids[0]].filter(Boolean).map(id=>document.getElementById('block-'+id)).filter(Boolean);
 if(!targets.length)return;let bottom=0,top=Infinity,height=Infinity;
 for(const el of targets){const page=el.closest('.pdf-page'),pane=el.closest('.pane-scroll');const y=el.getBoundingClientRect().top-page.getBoundingClientRect().top;top=Math.min(top,y);bottom=Math.max(bottom,y+el.getBoundingClientRect().height);height=Math.min(height,pane.clientHeight);}
 const shift=Math.min(Math.max(0,bottom-height+40),Math.max(0,top-40));
 for(const el of targets){const page=el.closest('.pdf-page'),pane=el.closest('.pane-scroll');setPaneTop(pane,pane.scrollTop+page.getBoundingClientRect().top-pane.getBoundingClientRect().top+shift);}
}
function textOffset(id){const el=document.getElementById('block-'+id)?.querySelector('.block-text');return el?el.getBoundingClientRect().top-el.closest('.pane-scroll').getBoundingClientRect().top:null;}
function alignText(r,clicked=null,member=null,offset=null){
 const ids=[member&&r.from_ids.includes(member)?member:r.from_ids[0],member&&r.to_ids.includes(member)?member:r.to_ids[0]].filter(Boolean);
 const targets=ids.map(id=>({id,el:document.getElementById('block-'+id)?.querySelector('.block-text')})).filter(x=>x.el);
 if(!targets.length)return;
 if(offset===null){const height=Math.min(...targets.map(x=>x.el.closest('.pane-scroll').clientHeight));const visible=targets.map(x=>textOffset(x.id)).find(y=>y>=24&&y<height*.65);offset=visible??Math.round(height*.22);}
 for(const {id,el} of targets){if(id===clicked)continue;const pane=el.closest('.pane-scroll');setPaneTop(pane,pane.scrollTop+el.getBoundingClientRect().top-pane.getBoundingClientRect().top-offset);}
}
function syncNavigation(){
 const list=app.querySelector('.nav-list'),r=related(active);if(!list||navClosed||!r)return;
 let entry=Array.from(list.querySelectorAll('.nav-entry')).find(el=>el.dataset.id===active);
 if(navMode==='chapters'){
  const selected=blocks().get(r.to_ids[0]??r.from_ids[0]);
  const heading=selected&&data.blocks.filter(b=>b.document_id===selected.document_id&&b.kind==='heading'&&b.order<=selected.order).sort((a,b)=>b.order-a.order)[0];
  entry=heading&&Array.from(list.querySelectorAll('.nav-entry')).find(el=>el.dataset.id===heading.id);
  list.querySelectorAll('.nav-entry').forEach(el=>el.setAttribute('aria-current',String(el===entry)));
 }
 // Respect search/detail filters. Only scroll this list, never the document panes.
 if(!entry)return;const box=entry.getBoundingClientRect(),viewport=list.getBoundingClientRect();
 if(box.top<viewport.top)list.scrollTop+=box.top-viewport.top;
 else if(box.bottom>viewport.bottom)list.scrollTop+=box.height>viewport.height?box.top-viewport.top:box.bottom-viewport.bottom;
}
function activate(id,clicked=null,member=null){
 if(modal.open)return;const r=related(id);if(!r)return;
 const clickedOffset=viewMode==='text'&&clicked?textOffset(clicked):null;
 const clickedEl=clicked?document.getElementById('block-'+clicked):null;const pageOffset=viewMode==='pages'&&clickedEl?clickedEl.closest('.pdf-page').getBoundingClientRect().top-clickedEl.closest('.pane-scroll').getBoundingClientRect().top:0;
 positions.push({active,scroll:scrollState()});if(positions.length>40)positions.shift();active=id;render();
 const role=clicked?data.source_documents.find(d=>d.id===blocks().get(clicked)?.document_id)?.role:null;
 if(viewMode==='text')alignText(r,clicked,member,clickedOffset);
 else {
 if(role!=='old'&&r.from_ids.length)scrollBlock(member&&r.from_ids.includes(member)?member:r.from_ids[0],pageOffset);
 if(role!=='new'&&r.to_ids.length)scrollBlock(member&&r.to_ids.includes(member)?member:r.to_ids[0],pageOffset);
 if(!clicked)revealRelationPages(r);
 }
 anchorScroll();syncNavigation();
}
async function transaction(fn){if(busy)return;busy=true;try{const before=E.clone(data),next=await fn();await E.verify(next);undo.push(before);redo.length=0;data=next;dirty=true;cache.clear();render();notify('修改已提交。相关说明与意见需复核，请导出保存。');}catch(e){notify(e.message);throw e;}finally{busy=false;}}
function show(html){modal.innerHTML=html;modal.showModal();modal.querySelector('textarea,input,button')?.focus();}
function closeModal(){modal.close();modal.innerHTML='';}
function editDialog(type,id){
 const b=type==='block'?blocks().get(id):data.reasons.find(x=>x.id===id);if(!b)return;
 const value=type==='block'?blockText(b):b.text,version=type==='block'?(data.working_copy.overrides[id]?.version??b.version):b.version;
 show(`<h2>${type==='block'?'编辑新稿 · '+esc(blockLabel(id)):'编辑原因'}</h2><p class="muted">提交后相关说明与判断需复核；不会自动同步正式报告。</p><textarea id="edit-text" aria-label="编辑文本">${esc(value)}</textarea><div class="actions">${button('submit-edit','提交','class="primary"')}${button('cancel','取消')}</div><p id="dialog-error" role="alert"></p>`);
 modal.querySelector('[data-action=submit-edit]').onclick=async()=>{const value=modal.querySelector('textarea').value;try{await transaction(()=>E.edit(data,type,id,value,version,'审阅者（未认证）'));closeModal();}catch(e){modal.querySelector('#dialog-error').textContent=e.message;}};
}
function remapDialog(){
 const r=related(active);let preview=null;
 const choices=role=>{const doc=data.source_documents.find(d=>d.role===role);return data.blocks.filter(b=>b.document_id===doc.id).sort((a,b)=>a.order-b.order).map(b=>`<label><input type="checkbox" data-side="${role}" value="${esc(b.id)}" ${(r?.[role==='old'?'from_ids':'to_ids']??[]).includes(b.id)?'checked':''}> ${esc(blockLabel(b.id))} · ${esc((blockText(b)||b.kind).slice(0,100))}${!blockRelation(b.id)?'〔未配对〕':''}</label>`).join('');};
 show(`<h2>调整对应</h2><p>每侧可多选，一侧可为空。先预览，再提交；正文不会删除。</p><div class="select-grid"><section><h3>旧稿</h3><div class="choices">${choices('old')}</div></section><section><h3>新稿</h3><div class="choices">${choices('new')}</div></section></div><div class="actions"><label>方式 <select id="remap-mode"><option value="replace">建立新对应</option><option value="release">解除所选对应</option></select></label><label><input type="checkbox" id="moved">跨位置移动</label><label>匹配置信度 <input type="number" id="confidence" value="50" min="0" max="100" style="width:70px">%</label></div><div class="actions">${button('preview','预览')}${button('commit-remap','确认改配','class="primary" disabled')}${button('cancel','取消')}</div><pre class="preview" id="preview">尚未预览</pre><p id="dialog-error" role="alert"></p>`);
 const confirm=modal.querySelector('[data-action=commit-remap]');
 modal.onchange=()=>{preview=null;confirm.disabled=true;modal.querySelector('#preview').textContent='选择已变化，请重新预览';};
 modal.querySelector('[data-action=preview]').onclick=()=>{try{
  const ids=role=>Array.from(modal.querySelectorAll(`input[data-side="${role}"]:checked`)).map(x=>x.value),confidence=Number(modal.querySelector('#confidence').value);
  if(!Number.isInteger(confidence)||confidence<0||confidence>100)throw Error('置信度必须是0—100整数');
  preview=E.preview(data,ids('old'),ids('new'),{mode:modal.querySelector('#remap-mode').value,moved:modal.querySelector('#moved').checked,confidence,actor:'审阅者（未认证）'});
  modal.querySelector('#preview').textContent='将替换：'+(preview.expected_relations.map(r=>r.id).join('、')||'无（来自未配对池）')+'\n新对应：\n'+preview.new_relations.map(r=>`${r.from_ids.join(' + ')||'空'} → ${r.to_ids.join(' + ')||'空'}`).join('\n')+'\n受影响的原因与意见保留旧锚点并标为待复核。';confirm.disabled=false;
 }catch(e){modal.querySelector('#dialog-error').textContent=e.message;}};
 confirm.onclick=async()=>{if(!preview)return;try{await transaction(()=>E.remap(data,preview));active=preview.new_relations[0]?.id??null;closeModal();render();}catch(e){modal.querySelector('#dialog-error').textContent=e.message;}};
}
function staticHtml(d){
 const oldData=data;data=d;try{
 const body=['old','new'].map(role=>{const doc=d.source_documents.find(x=>x.role===role);return `<section><h2>${role==='old'?'旧稿':'新稿'} · ${esc(doc.report_version)}</h2>${d.blocks.filter(b=>b.document_id===doc.id).sort((a,b)=>a.order-b.order).map(b=>`<article class="static-block" data-block-id="${esc(b.id)}"><small>${esc(b.id)} · ${esc(b.source_locator)}</small><div>${d.page_previews&&b.content.type==='image'?esc(b.content.caption):content(b)}</div></article>`).join('')}</section>`;}).join('');
 const notes=['reasons','changes','opinions','decisions','relation_history'].map(k=>`<section><h3>${esc(({reasons:'修改原因',changes:'影响标注',opinions:'专家意见与处置',decisions:'决策依据',relation_history:'对应替换历史'})[k])}</h3>${d[k].map(x=>`<pre>${esc(JSON.stringify(x,null,2))}</pre>`).join('')}</section>`).join('');
 let pageHtml='';if(d.page_previews)pageHtml='<div class="static-panes">'+['old','new'].map(role=>{const doc=d.source_documents.find(x=>x.role===role);return '<section><h2>'+(role==='old'?'旧稿':'新稿')+' · '+esc(doc.report_version)+'</h2>'+d.page_previews[doc.id].pages.map(p=>`<figure class="static-page"><img src="${esc(p.data_url)}" alt="PDF第${p.number}页"><figcaption>第${p.number}页</figcaption></figure>`).join('')+'</section>';}).join('')+'</div>';
 return pageHtml+`<header><h1>${esc(d.title)}</h1><p>审阅 r${d.review_revision} · ${esc(L(d.working_copy.report_sync))}</p><p>${esc(d.summary.text)} · ${esc(L(d.summary.review_status))}</p></header><details open><summary>来源与覆盖</summary>${d.source_documents.map(x=>`<p>${esc(x.filename)} · ${esc(x.report_version)} · SHA-256 ${esc(x.sha256)}</p><pre>${esc(JSON.stringify(x.coverage,null,2))}</pre>`).join('')}</details><div class="static-panes">${body}</div><section class="static-relations"><h2>对应关系</h2>${d.relations.map(x=>`<p>${esc(x.id)} · ${esc(L(x.kind))} · ${esc(x.from_ids.join(' + '))} → ${esc(x.to_ids.join(' + '))} · ${esc(L(x.review_status))}</p>`).join('')}</section><section class="static-notes">${notes}</section>`;
 }finally{data=oldData;}
}
function printHtml(d,summaryOnly=false){
 const prior=data;data=d;try{
 const header=`<header><h1>${esc(d.title)}</h1><p>审阅 r${d.review_revision} · ${esc(L(d.working_copy.report_sync))}</p><p>${esc(d.summary.text)} ${chip(d.summary.review_status)}</p><p class="print-context">图件与表格为阅读副本；页眉页脚等覆盖限制见来源。意见仅按已登记来源展示。打印不回写正式报告。</p>${d.source_documents.map(x=>`<p class="print-context">${esc(x.filename)} · ${esc(x.report_version)}<br>SHA-256 ${esc(x.sha256)}<br>遗漏：${esc(x.coverage.omissions.join('；')||'支持范围内未登记遗漏')}</p>`).join('')}</header>`;
 const notes=d.reasons.map(r=>`<section><h2>修改说明 ${esc(r.id)}</h2><p>${esc(r.text)} ${chip(r.review_status)}</p><p class="print-context">${esc(r.source)}；${esc(r.evidence)}</p></section>`).join('')+d.opinions.map(opinionHtml).join('')+d.decisions.map(x=>`<section><h2>待决定</h2><p>${esc(x.question)}</p><p>${esc(x.basis)}</p></section>`).join('');
 if(summaryOnly)return header+notes;
 const bs=blocks(),documents=new Map(d.source_documents.map(x=>[x.role,x]));
 const pairs=d.relations.map(r=>({r,old:r.from_ids,new:r.to_ids}));
 d.blocks.filter(b=>!blockRelation(b.id)).forEach(b=>{const role=d.source_documents.find(x=>x.id===b.document_id).role;pairs.push({r:null,old:role==='old'?[b.id]:[],new:role==='new'?[b.id]:[]});});
 return header+pairs.map((item,i)=>`<article class="print-group"><h2>条目 ${i+1} · ${esc(item.r?.id??'未配对')} · ${esc(L(item.r?.kind??'未配对'))} ${item.r?.moved?'（移动）':''}</h2><div class="print-pair">${['old','new'].map(role=>`<section><h3>${role==='old'?'旧稿':'新稿'} · ${esc(documents.get(role).report_version)}</h3>${item[role].length?item[role].map(id=>`<small>${esc(id)} · ${esc(bs.get(id).source_locator)}</small><div class="print-text">${content(bs.get(id),segmentsFor(item.r,bs.get(id),item.r?diffFor(item.r):null))}</div>`).join(''):'<p>〔无对应内容〕</p>'}</section>`).join('')}</div>${d.reasons.filter(x=>x.relation_ids.includes(item.r?.id)).map(x=>`<p>原因：${esc(x.text)} ${chip(x.review_status)}</p>`).join('')}</article>`).join('')+notes;
 }finally{data=prior;}
}
const jsonScript=x=>E.canonical(x).replaceAll('&','\\u0026').replaceAll('<','\\u003c').replaceAll('>','\\u003e').replaceAll('\u2028','\\u2028').replaceAll('\u2029','\\u2029');
async function serialize(d){
 await E.verify(d);const script=document.getElementById('app-code').textContent,css=document.getElementById('app-style').textContent,csp=document.querySelector('meta[http-equiv="Content-Security-Policy"]').content;
 const close='</'+'script>';
 return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+`<meta http-equiv="Content-Security-Policy" content="${esc(csp)}"><title>${esc(d.title)}</title><style id="app-style">${css}</style></head><body><div id="static-view">${staticHtml(d)}</div><div id="app" hidden></div><dialog id="modal"></dialog><script id="revision-package" type="application/json">${jsonScript(d)}${close}<script id="app-code">${script}${close}</body></html>\n`;
}
function download(text,name,type){const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function act(action,el){
 switch(action){
 case 'fullscreen':try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.fullscreenEnabled&&document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else notify('当前浏览器不支持页面全屏，可在浏览器中使用 F11。');}catch(e){notify('未能进入全屏，请直接在浏览器中打开此 HTML 后重试，或使用 F11。');}break;
 case 'view-pages':case 'view-text':viewMode=action==='view-pages'?'pages':viewMode==='text'?'pages':'text';render();if(active){const r=related(active);if(viewMode==='text')alignText(r);else{if(r?.from_ids[0])scrollBlock(r.from_ids[0]);if(r?.to_ids[0])scrollBlock(r.to_ids[0]);revealRelationPages(r);}}anchorScroll();break;
 case 'pdf-block':{const r=blockRelation(el.dataset.id);if(r)activate(r.id,el.dataset.id);else notify('本块尚未配对，请在文字审阅中核对。');break;}
 case 'detail-revisions':showDetails=el.checked;if(!showDetails&&related(active)&&isDetailRevision(related(active)))active=data.relations.find(visibleChange)?.id??null;render();notify(showDetails?'已显示细节修订':'已隐藏日期、版本等细节修订；原文保留显示。');break;
 case 'next-change':{const changed=data.relations.filter(visibleChange),i=changed.findIndex(r=>r.id===active),next=changed[(i+1)%changed.length];if(next){activate(next.id);notify(`修改 ${((i+1)%changed.length)+1} / ${changed.length}${i===changed.length-1?' · 已回到第一处':''}`);}break;}
 case 'nav-mode':navMode=navMode==='changes'?'chapters':'changes';render();syncNavigation();break;
 case 'nav':navClosed=!navClosed;render();break;
 case 'dock':dockClosed=!dockClosed;render();break;
 case 'activate':activate(el.dataset.id);break;
 case 'block':{const b=blocks().get(el.dataset.id),r=blockRelation(el.dataset.id);if(r)activate(r.id,null,b.id);else{positions.push({active,scroll:scrollState()});active=null;render();scrollBlock(b.id);document.getElementById('block-'+b.id)?.classList.add('active');notify('此块未配对，未自动猜配。');}break;}
 case 'member':scrollBlock(el.dataset.id);anchorScroll();break;
 case 'back':{const p=positions.pop();if(p){active=p.active;render();restoreScroll(p.scroll);anchorScroll();syncNavigation();}break;}
 case 'edit-block':editDialog('block',el.dataset.id);break;
 case 'edit-reason':editDialog('reason',el.dataset.id);break;
 case 'confirm-reason':case 'confirm-impact':await transaction(()=>E.confirmReview(data,action==='confirm-reason'?'reason':'impact',el.dataset.id,'审阅者（未认证）'));notify('已登记本人核对当前说明；专家意见处置与正式报告同步状态不变。');break;
 case 'remap':remapDialog();break;
 case 'cancel':closeModal();break;
 case 'undo':if(undo.length){redo.push(E.clone(data));data=undo.pop();dirty=true;cache.clear();render();}break;
 case 'redo':if(redo.length){undo.push(E.clone(data));data=redo.pop();dirty=true;cache.clear();render();}break;
 case 'export':if(modal.open)return;download(await serialize(data),`${data.title.replace(/[\\/:*?"<>|]/g,'_')}_${data.source_documents.find(d=>d.role==='old').report_version}_至_${data.source_documents.find(d=>d.role==='new').report_version}_审阅r${data.review_revision}.html`,'text/html;charset=utf-8');dirty=false;render();notify('导出请求已发出；请检查下载文件。本页无法确认磁盘保存是否完成。');break;
 case 'patch':download(JSON.stringify(await E.patch(base,data),null,2),`${data.package_id}_r${base.review_revision}_至_r${data.review_revision}.json`,'application/json');notify('补丁下载请求已发出，正文仍需导出完整 HTML。');break;
 case 'sources':show(`<h2>来源与提取覆盖</h2>${data.source_documents.map(d=>`<h3>${esc(d.filename)}</h3><p>${esc(d.report_version)} · ${esc(d.identity_kind)}</p><p>SHA-256：${esc(d.sha256)}</p><pre style="white-space:pre-wrap">${esc(JSON.stringify(d.coverage,null,2))}</pre>`).join('')}${button('cancel','关闭')}`);break;
 case 'print':show(`<h2>打印阅读版</h2><p>长段允许续页，正式报告格式仍以 DOCX/PDF 为准。</p><div class="actions">${button('print-full','打印完整对照')}${button('print-summary','仅打印摘要/说明')}${button('cancel','取消')}</div>`);break;
 case 'print-full':case 'print-summary':{printSummary=action==='print-summary';document.getElementById('static-view').innerHTML=printHtml(data,printSummary);closeModal();window.print();break;}
 }
}
app.addEventListener('scroll',e=>{if(e.target.classList?.contains('pane-scroll'))syncPaneScroll(e.target);},true);
app.addEventListener('click',e=>{
 const btn=e.target.closest('[data-action]');if(btn){act(btn.dataset.action,btn).catch(err=>notify(err.message));return;}
 const image=e.target.closest('[data-image]');if(image){const b=blocks().get(image.dataset.image);show(`<h2>${esc(b.content.caption)}</h2><img class="large-image" src="${esc(b.content.data_url)}" alt="${esc(b.content.alt)}">${button('cancel','关闭')}`);return;}
 if(selectionBusy())return;const block=e.target.closest('[data-block-id]');if(block){const r=blockRelation(block.dataset.blockId);if(r)activate(r.id,block.dataset.blockId);else notify('本块未配对，可使用“调整对应”。');}
});
modal.addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(b&&['cancel','block','print-full','print-summary'].includes(b.dataset.action)){if(b.dataset.action==='block'){closeModal();}act(b.dataset.action,b).catch(err=>notify(err.message));}});
modal.addEventListener('cancel',e=>{if(modal.querySelector('textarea')&&modal.querySelector('textarea').value!==modal.querySelector('textarea').defaultValue&&!confirm('放弃尚未提交的草稿？'))e.preventDefault();});
modal.addEventListener('close',()=>{modal.innerHTML='';modal.onchange=null;});
app.addEventListener('input',e=>{if(e.target.id==='search'){query=e.target.value;app.querySelector('.nav-list').innerHTML=navEntries();}});
app.addEventListener('change',e=>{if(e.target.id==='show-detail-revisions'){act('detail-revisions',e.target).catch(err=>notify(err.message));return;}if(e.target.id==='filter'){filter=e.target.value;app.querySelector('.nav-list').innerHTML=navEntries();}});
document.addEventListener('compositionstart',()=>composing=true);document.addEventListener('compositionend',()=>composing=false);
document.addEventListener('keydown',e=>{if(e.isComposing||selectionBusy()||modal.open)return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();act(e.shiftKey?'redo':'undo',null);}if(e.key==='Enter'){const a=e.target.closest('[data-action]');if(a&&a.tagName!=='BUTTON'){act(a.dataset.action,a).catch(err=>notify(err.message));return;}const b=e.target.closest('[data-block-id]');if(b){const r=blockRelation(b.dataset.blockId);if(r)activate(r.id,b.dataset.blockId);}}});
document.addEventListener('fullscreenchange',()=>{const b=app.querySelector('[data-action=fullscreen]');if(b){b.textContent=document.fullscreenElement?'退出全屏':'全屏显示';b.setAttribute('aria-pressed',String(!!document.fullscreenElement));}requestAnimationFrame(()=>anchorScroll());});
window.addEventListener('resize',()=>anchorScroll());
window.addEventListener('beforeunload',e=>{if(dirty||modal.querySelector('textarea')){e.preventDefault();e.returnValue='';}});
window.addEventListener('beforeprint',()=>{document.getElementById('static-view').innerHTML=printHtml(data,printSummary);});
window.RevisionApp={getCurrentPackage:()=>E.clone(data),serializeOfflineHtml:()=>serialize(data),getBasePackage:()=>E.clone(base)};
(async()=>{try{await E.verify(data);active=(data.relations.find(isImportant)??data.relations.find(r=>r.content_changed)??data.relations[0])?.id??null;render();document.getElementById('static-view').hidden=true;app.hidden=false;if(active){const r=related(active);if(viewMode==='text')alignText(r);else{if(r.from_ids[0])scrollBlock(r.from_ids[0]);if(r.to_ids[0])scrollBlock(r.to_ids[0]);revealRelationPages(r);}}anchorScroll();syncNavigation();}catch(e){document.getElementById('static-view').insertAdjacentHTML('afterbegin',`<p role="alert">交互未启动：${esc(e.message)}。以下为静态内容。</p>`);}})();
})();

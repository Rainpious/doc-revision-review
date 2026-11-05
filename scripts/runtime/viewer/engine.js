/* Normalized state transactions. No DOM, network, or third-party code. */
(function(global){
'use strict';
const clone=x=>JSON.parse(JSON.stringify(x));
const canonical=x=>x===null||typeof x!=='object'?JSON.stringify(x):Array.isArray(x)?'['+x.map(canonical).join(',')+']':'{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';
async function hash(d){const x=clone(d);delete x.working_copy.state_hash;return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(x))))).map(v=>v.toString(16).padStart(2,'0')).join('');}
async function seal(d){d.working_copy.state_hash=await hash(d);return d;}
const id=prefix=>prefix+crypto.randomUUID().replaceAll('-','');
const text=(d,b)=>d.working_copy.overrides[b.id]?.text??b.content.text??'';
function kind(a,b){if(!a.length&&!b.length)throw Error('两侧不能都为空');return !a.length?'addition':!b.length?'deletion':a.length===1&&b.length===1?'correspondence':a.length===1?'split':b.length===1?'merge':'reorganization';}
function invalidate(d,ids){
 const affected=new Set(d.changes.filter(c=>ids.has(c.relation_id)).map(c=>c.id));
 d.relations.filter(r=>ids.has(r.id)).forEach(r=>{r.review_status='needs_review';r.diff_status='stale';});
 d.reasons.filter(r=>r.relation_ids.some(x=>ids.has(x))).forEach(r=>r.review_status='needs_review');
 const opids=new Set();d.changes.filter(c=>affected.has(c.id)).forEach(c=>{c.review_status=c.impact.review_status='needs_review';c.opinion_ids.forEach(x=>opids.add(x));});
 const bids=new Set(d.relations.filter(r=>ids.has(r.id)).flatMap(r=>[...r.from_ids,...r.to_ids]));
 d.opinions.filter(o=>opids.has(o.id)||o.target.block_ids.some(x=>bids.has(x))).forEach(o=>o.review_status='needs_review');
 d.decisions.filter(x=>x.change_ids.some(c=>affected.has(c))).forEach(x=>x.review_status='needs_review');d.summary.review_status='needs_review';
}
async function verify(d){if(await hash(d)!==d.working_copy.state_hash)throw Error('数据哈希不一致');}
async function edit(data,type,target,value,expected,actor='unknown'){
 await verify(data);const d=clone(data);let old,version,ids;
 if(type==='block'){
  const b=d.blocks.find(b=>b.id===target);if(!b?.editable)throw Error('此块不允许编辑');
  old=text(d,b);version=d.working_copy.overrides[target]?.version??b.version;
  d.working_copy.overrides[target]={text:value,version:version+1};d.working_copy.report_sync='unsynced';
  ids=new Set(d.relations.filter(r=>r.to_ids.includes(target)).map(r=>r.id));
 }else if(type==='reason'){
  const r=d.reasons.find(r=>r.id===target);if(!r)throw Error('原因不存在');
  old=r.text;version=r.version;r.text=value;r.version++;r.review_status='needs_review';ids=new Set(r.relation_ids);
 }else throw Error('未知编辑对象');
 if(version!==expected)throw Error('内容版本冲突');invalidate(d,ids);
 if(type==='block')for(const r of d.relations.filter(r=>ids.has(r.id))){
  const members=[...r.from_ids,...r.to_ids].map(x=>d.blocks.find(b=>b.id===x));
  if(members.every(b=>b.content.type==='text'))r.content_changed=r.from_ids.map(x=>text(d,d.blocks.find(b=>b.id===x))).join('\n')!==r.to_ids.map(x=>text(d,d.blocks.find(b=>b.id===x))).join('\n');
 }
 d.parent_revision=data.review_revision;d.review_revision++;
 d.operations.push({id:id('op-'),kind:'edit_text',actor,at:new Date().toISOString(),target_type:type,target_id:target,expected_version:expected,old_value:old,new_value:value,base_revision:data.review_revision,base_hash:data.working_copy.state_hash});
 return seal(d);
}
function preview(data,from,to,{mode='replace',moved=false,confidence=50,actor='unknown'}={}){
 const selected=[...from,...to],picked=new Set(selected);if(!selected.length||picked.size!==selected.length)throw Error('选择为空或重复');
 if(!['replace','release'].includes(mode))throw Error('改配方式错误');
 for(const [ids,role]of [[from,'old'],[to,'new']])for(const bid of ids){const b=data.blocks.find(b=>b.id===bid);if(!b||data.source_documents.find(x=>x.id===b.document_id)?.role!==role)throw Error('选择的块不在正确一侧');}
 const affected=data.relations.filter(r=>[...r.from_ids,...r.to_ids].some(b=>picked.has(b))),next=[];
 function create(a,b,m){a=[...a].sort((x,y)=>data.blocks.find(b=>b.id===x).order-data.blocks.find(b=>b.id===y).order);b=[...b].sort((x,y)=>data.blocks.find(b=>b.id===x).order-data.blocks.find(b=>b.id===y).order);next.push({id:id('r-'),from_ids:a,to_ids:b,kind:kind(a,b),moved:m,content_changed:true,method:'human',confidence,review_status:'needs_review',diff_status:'stale'});}
 if(mode==='replace')create(from,to,moved);
 for(const r of affected){const a=r.from_ids.filter(x=>!picked.has(x)),b=r.to_ids.filter(x=>!picked.has(x));if(a.length||b.length)create(a,b,r.moved);}
 return {id:id('op-'),kind:'remap',actor,at:new Date().toISOString(),base_revision:data.review_revision,base_hash:data.working_copy.state_hash,mode,selected_from_ids:[...from],selected_to_ids:[...to],expected_relations:clone(affected),new_relations:next};
}
async function remap(data,p){
 await verify(data);if(p.base_hash!==data.working_copy.state_hash||p.base_revision!==data.review_revision)throw Error('预览已过期，请重新预览');
 const expected=preview(data,p.selected_from_ids,p.selected_to_ids,{mode:p.mode});
 if(canonical(expected.expected_relations)!==canonical(p.expected_relations))throw Error('旧对应关系冲突');
 const original=new Set(p.expected_relations.flatMap(r=>[...r.from_ids,...r.to_ids])),selected=new Set([...p.selected_from_ids,...p.selected_to_ids]);
 const required=p.mode==='replace'?new Set([...original,...selected]):new Set([...original].filter(x=>!selected.has(x)));
 const actual=p.new_relations.flatMap(r=>[...r.from_ids,...r.to_ids]);
 if(new Set(actual).size!==actual.length||actual.length!==required.size||actual.some(x=>!required.has(x)))throw Error('改配会丢失或重复成员');
 const d=clone(data),retired=new Set(p.expected_relations.map(r=>r.id));invalidate(d,retired);
 d.relations=d.relations.filter(r=>!retired.has(r.id)).concat(clone(p.new_relations));
 d.relation_history.push({retired_ids:[...retired].sort(),replacement_ids:p.new_relations.map(r=>r.id),at:p.at,actor:p.actor,unresolved_reason_ids:d.reasons.filter(r=>r.relation_ids.some(x=>retired.has(x))).map(r=>r.id),unresolved_opinion_ids:d.opinions.filter(o=>o.target.block_ids.some(x=>original.has(x))).map(o=>o.id)});
 d.parent_revision=data.review_revision;d.review_revision++;d.operations.push(clone(p));return seal(d);
}
async function patch(base,current){
 await verify(base);await verify(current);
 if(base.package_id!==current.package_id||canonical(base.source_documents)!==canonical(current.source_documents))throw Error('补丁来源身份冲突');
 const count=base.operations.length;if(canonical(current.operations.slice(0,count))!==canonical(base.operations))throw Error('操作历史不连续');
 const ops=current.operations.slice(count);let replay=clone(base);
 for(const op of ops){
  if(op.base_hash!==replay.working_copy.state_hash||op.base_revision!==replay.review_revision)throw Error('操作基线冲突');
  let next=op.kind==='remap'?await remap(replay,op):op.kind==='confirm_review'?await confirmReview(replay,op.target_type,op.target_id,op.actor):await edit(replay,op.target_type,op.target_id,op.new_value,op.expected_version,op.actor);
  if(op.kind==='confirm_review'&&next.operations.at(-1).old_status!==op.old_status)throw Error('确认状态冲突');
  if(op.kind==='edit_text'&&next.operations.at(-1).old_value!==op.old_value)throw Error('补丁旧值冲突');
  next.operations[next.operations.length-1]=clone(op);replay=await seal(next);
 }
 if(canonical(replay)!==canonical(current))throw Error('存在未登记修改，不能导出补丁');
 return {schema_version:'1.0',package_id:base.package_id,base_revision:base.review_revision,base_hash:base.working_copy.state_hash,source_documents:clone(base.source_documents),result_revision:current.review_revision,result_hash:current.working_copy.state_hash,operations:clone(ops)};
}
async function confirmReview(data,type,target,actor='unknown'){
 await verify(data);const d=clone(data),active=new Set(d.relations.map(r=>r.id));let item;
 if(type==='reason'){item=d.reasons.find(r=>r.id===target);if(item&&item.relation_ids.some(x=>!active.has(x)))throw Error('旧原因锚点需先由制作端重新关联');}
 else if(type==='impact'){const c=d.changes.find(c=>c.id===target);if(c&&!active.has(c.relation_id))throw Error('旧影响锚点需先重新关联');item=c?.impact;}
 else throw Error('仅可确认本人核对的原因或影响说明');
 if(!item)throw Error('确认对象不存在');const old=item.review_status;item.review_status='reviewed';
 d.parent_revision=data.review_revision;d.review_revision++;
 d.operations.push({id:id('op-'),kind:'confirm_review',actor,at:new Date().toISOString(),base_revision:data.review_revision,base_hash:data.working_copy.state_hash,target_type:type,target_id:target,old_status:old});return seal(d);
}
/* Bounded character LCS, Chinese/codepoint aware; large groups degrade explicitly. */
function diff(a,b){
 if(a===b)return {old:[{text:a,changed:false}],new:[{text:b,changed:false}],status:'current'};
 const x=Array.from(a),y=Array.from(b);let start=0,end=0;
 while(start<Math.min(x.length,y.length)&&x[start]===y[start])start++;
 while(end<Math.min(x.length,y.length)-start&&x[x.length-1-end]===y[y.length-1-end])end++;
 const u=x.slice(start,x.length-end),v=y.slice(start,y.length-end);
 if(u.length*v.length>1500000)return {old:[{text:a,changed:false}],new:[{text:b,changed:false}],status:'unsupported'};
 const matrix=Array.from({length:u.length+1},()=>new Uint32Array(v.length+1));
 for(let i=u.length-1;i>=0;i--)for(let j=v.length-1;j>=0;j--)matrix[i][j]=u[i]===v[j]?1+matrix[i+1][j+1]:Math.max(matrix[i+1][j],matrix[i][j+1]);
 const left=[],right=[];const push=(list,t,c)=>{if(!t)return;const last=list.at(-1);if(last&&last.changed===c)last.text+=t;else list.push({text:t,changed:c});};
 push(left,x.slice(0,start).join(''),false);push(right,y.slice(0,start).join(''),false);let i=0,j=0;
 while(i<u.length||j<v.length){if(i<u.length&&j<v.length&&u[i]===v[j]){push(left,u[i++],false);push(right,v[j++],false);}else if(i<u.length&&(j===v.length||matrix[i+1][j]>=matrix[i][j+1]))push(left,u[i++],true);else push(right,v[j++],true);}
 push(left,x.slice(x.length-end).join(''),false);push(right,y.slice(y.length-end).join(''),false);
 return {old:left,new:right,status:'current'};
}
global.RevisionEngine={clone,canonical,hash,seal,verify,text,kind,edit,preview,remap,patch,confirmReview,diff};
})(globalThis);

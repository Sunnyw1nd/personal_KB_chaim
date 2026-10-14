"use strict";
let token, current, selectedKey, docId, detail;
let importing=false, lastFiles;
const sourceLabels={wechat_saved_html:'微信',zhihu_column_saved_html:'知乎',local_text:'TXT',local_markdown:'Markdown',local_image:'图片'};
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const show = text => {$('message').textContent=text;};
async function api(path, data) {
 const opts={}; if(data!==undefined){opts.method='POST';opts.headers={'X-Workbench-Token':token};
 if(data instanceof FormData)opts.body=data;else{opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}}
 const r=await fetch(path,opts); const body=await r.json();if(!r.ok)throw Error(body.error||r.status);return body;
}
const query=(extra={})=>new URLSearchParams({doc:docId,rev:current.document.revision.revisionId,...extra}).toString();
const binding=()=>({documentId:docId,revisionId:current.document.revision.revisionId,key:selectedKey});
function imageFor(asset){const img=node('img');img.src='/api/asset?'+query({asset});img.alt='正文原图';return img;}
function readable(value){return JSON.stringify(value,null,2);}
async function documents(){
 const docs=await api('/api/documents');$('documents').replaceChildren();const term=$('search').value.toLowerCase();
 for(const d of docs){if(d.error){$('documents').append(node('p',d.error));continue;}if(!(d.title+' '+d.source).toLowerCase().includes(term))continue;
 const b=node('button',d.title+' · '+(sourceLabels[d.source]||d.source)+' · '+d.documentId.slice(-6)+' · 缺图 '+d.completeness.missing);b.dataset.documentId=d.documentId;b.onclick=()=>openDoc(d.documentId);$('documents').append(b);}
}
async function openDoc(id,rev){docId=id;selectedKey=null;current=await api('/api/document?'+new URLSearchParams({doc:id,...(rev?{rev}:{})}));
 $('title').textContent=current.document.metadata.title;$('revision').replaceChildren();
 for(const r of current.record.revisions){const o=node('option',r.revisionId+(r.revisionId===current.record.currentRevisionId?' · current':' · 历史'));o.value=r.revisionId;$('revision').append(o);}
 $('revision').value=current.document.revision.revisionId;$('completeness').textContent='图片完整性：'+readable(current.document.assetCompleteness);
 $('updateLabel').hidden=!id.startsWith('local-');
 const source=current.document.source;const asset=current.document.assets[0];$('sourceInfo').textContent=(sourceLabels[current.document.metadata.sourceType]||current.document.metadata.sourceType)+' · 输入：'+(source.inputFileName||source.htmlFileName)+' · '+(source.encoding||source.htmlEncoding||source.mediaType||'')+(source.format==='image'&&asset?' · '+asset.width+' × '+asset.height+' · '+asset.sizeBytes+' 字节':'')+' · '+current.document.processing.status;
 $('reports').textContent=readable({processing:current.processingReport,resources:current.resourceManifest});renderBody();renderGroups();$('review').replaceChildren();
 sessionStorage.setItem('documentSelection',JSON.stringify({id,rev:current.document.revision.revisionId}));
}
function renderBody(){
 $('body').replaceChildren();for(const b of current.document.blocks){let e;
 if(b.type==='image'){const valid=current.groups.find(g=>g.locations.some(l=>l.assetId===b.assetId));e=valid?imageFor(b.assetId):node('p','[缺失／不支持图片] '+b.assetId);if(b.caption){const f=node('figure');f.append(e,node('figcaption',b.caption));e=f;}}
 else if(b.type==='table'){e=node('table');for(const row of b.rows){const tr=node('tr');for(const cell of row)tr.append(node('td',cell));e.append(tr);}}
 else if(b.type==='list'){e=node(b.ordered?'ol':'ul');if(b.ordered&&b.items[0]?.number!==undefined)e.start=b.items[0].number;let stack=[e];for(const item of b.items){const depth=Math.min(item.depth||0,30);while(stack.length>depth+1)stack.pop();while(stack.length<depth+1){const sub=node(item.ordered?'ol':'ul');if(item.number!==undefined)sub.start=item.number;const parent=stack.at(-1);if(!parent.lastElementChild)parent.append(node('li',''));parent.lastElementChild.append(sub);stack.push(sub);}const li=node('li');if(item.number!==undefined)li.value=item.number;renderInlines(li,item.inlines,item.content);stack.at(-1).append(li);}}
 else if(b.originalTag==='thematic_break')e=node('hr');
 else if(b.plainText)e=node('pre',b.content||'','code');
 else e=node(b.type==='heading'?'h'+b.level:b.type==='code'?'pre':b.type==='quote'?'blockquote':'p',b.content||'[未支持内容]',b.type==='code'?'code':null);
 if(b.inlines&&b.type!=='code')renderInlines(e,b.inlines,b.content);
 e.dataset.blockId=b.blockId;$('body').append(e);}
}
function renderInlines(root,spans,fallback){root.replaceChildren();if(!spans?.length){root.textContent=fallback||'';return;}for(const span of spans){let e=document.createTextNode(span.text);for(const mark of span.marks||[]){const wrap=node(mark==='strong'?'strong':mark==='code'?'code':'em');wrap.append(e);e=wrap;}if(span.href&&/^(https?:|mailto:)/i.test(span.href)){const a=node('a');a.href=span.href;a.rel='noopener noreferrer';a.target='_blank';a.append(e);e=a;}root.append(e);}}
function renderGroups(){
 $('pictures').replaceChildren();$('consent').checked=false;
 for(const g of current.groups){const box=node('div',undefined,'group');box.append(imageFor(g.locations[0].assetId));const content=node('div');
 const label=node('label');const cb=node('input');cb.type='checkbox';cb.value=g.key;cb.disabled=g.localOnly||g.cache==='cache_review_rejected';cb.onchange=budget;label.append(cb,document.createTextNode(' 位置 '+g.locations.map(l=>l.order).join(',')+' · '+g.cache+' · '+g.reviewStatus+(g.localOnly?' · 禁止外发':'')));content.append(label);
 content.append(node('p',g.image.mediaType+' · '+g.image.width+' × '+g.image.height+' · '+g.image.sizeBytes+' 字节'));
 const context=node('details');context.append(node('summary','查看必要上下文'),node('pre',readable(g.context)));content.append(context);
 const b=node('button','查看结果／审核');b.onclick=()=>openResult(g.key);content.append(b);box.append(content);$('pictures').append(box);}
 $('excluded').textContent=current.excluded.length?'无法处理的正文资源：'+readable(current.excluded):'';budget();
}
function keys(){return [...document.querySelectorAll('#pictures input:checked')].map(c=>c.value);}
function budget(){const chosen=keys();const fresh=current?current.groups.filter(g=>chosen.includes(g.key)&&g.cache==='not_processed').length:0;$('budget').textContent=`选定 ${chosen.length} 个图片/上下文任务；最多 ${chosen.length*3} 次调用（待生成最多 ${fresh*3} 次），缓存不计费；缺密钥不会伪装成功。`;}
async function upload(files,target=null,createNew=false){if(importing)return;importing=true;lastFiles=[...files];try{const f=new FormData();for(const file of files)f.append('files',file);f.append('requestId',crypto.randomUUID());if(target)f.append('targetDocumentId',target);if(createNew)f.append('createNew','true');if($('inputEncoding').value)f.append('encoding',$('inputEncoding').value);if($('mainMarkdown').value.trim())f.append('mainMarkdown',$('mainMarkdown').value.trim());const task=await api('/api/import',f);show('导入已排队 '+task.taskId);const done=await pollTask(task.taskId);renderImports(done.items);await documents();const row=done.items.find(i=>i.documentId);if(row)await openDoc(row.documentId);return done;}finally{importing=false;$('upload').value='';$('updateUpload').value='';}}
function renderImports(items){const root=$('importResult');root.replaceChildren();const filesSnapshot=[...lastFiles];for(const [index,item] of items.entries()){const row=node('div');row.append(node('p',(item.originalName||'')+' · '+(item.error||item.decision||'')));if(item.documentId){const view=node('button','查看 '+(item.title||item.documentId));view.onclick=handle(()=>openDoc(item.documentId));row.append(view);}if(item.candidates){row.append(node('p','发现相同输入记录；不会自动合并。请选择：'));for(const candidate of item.candidates){const update=node('button','明确更新 '+candidate.title);update.onclick=handle(()=>upload([filesSnapshot[index]],candidate.documentId));row.append(update);}const fresh=node('button','仍作为新文档导入');fresh.onclick=handle(()=>upload([filesSnapshot[index]],null,true));row.append(fresh);}root.append(row);}}
async function pollTask(id){
 for(;;){const t=await api('/api/task/'+id);$('importResult').textContent=t.operation==='import'?readable(t.items):$('importResult').textContent;
 show(`${t.operation} · ${t.state} · 已完成 ${t.items.length} · ${readable(t.progress||[])}`);await tasks();
 if(!['running','queued'].includes(t.state)){show(t.error||'任务结束：'+t.state+'；结构通过不等于内容已确认。');return t;}await new Promise(r=>setTimeout(r,600));}
}
async function enrich(newRun=false){if(!current||!keys().length)throw Error('先选正文图片');if(!$('consent').checked)throw Error('请确认所选图片和上下文的外发授权');
 const scope=readable({docId,rev:current.document.revision.revisionId,keys:keys().sort()});let id=sessionStorage.getItem('submit:'+scope);
 if(!id||newRun){id=crypto.randomUUID();sessionStorage.setItem('submit:'+scope,id);}
 if(newRun&&!confirm('这是新的明确操作；仍优先缓存，不绕过否决。未知请求可能已计费。继续？'))return;
 const task=await api('/api/enrich',{documentId:docId,revisionId:current.document.revision.revisionId,keys:keys(),consent:true,requestId:id,newRun});
 await pollTask(task.taskId);await openDoc(docId,current.document.revision.revisionId);}
async function openResult(key){selectedKey=key;detail=await api('/api/result?'+query({key}));const root=$('review');root.replaceChildren();
 const pair=node('div',undefined,'pair');pair.append(imageFor(detail.binding.locations[0].assetId));const content=node('div');const record=detail.record;
 content.append(node('h3','模型原结果 · '+(record?.reviewStatus||'未运行')+' · '+(detail.issue||'结构可读取、尚需质量审核')));
 content.append(node('pre',record?readable(record.result):'没有已保存结果'));
 if(detail.machineReviewDecision)content.append(node('pre','当前机器审核依据：'+readable(detail.machineReviewDecision)));
 if(record?.result?.code)content.append(node('pre',record.result.code.text,'code'));
 const raw=node('details');raw.append(node('summary','首次／恢复 attempt 与脱敏原文'),node('pre',readable(detail.attempts)));content.append(raw);
 if(record){for(const [s,label] of [['approved','认可机器结果'],['needs_review','待复核'],['rejected','否决机器结果']]){const b=node('button',label);b.dataset.review=s;b.onclick=async()=>{const reason=prompt('请输入审核依据（不发送给模型）');if(!reason)return;await api('/api/review',{...binding(),status:s,reason});await openResult(key);};content.append(b);}}
 pair.append(content);root.append(pair);if(!record)return;
 const adopted=detail.human.versions.find(v=>v.revisionId===detail.human.adopted?.revisionId);const source=adopted||record.result||{};
 root.append(node('h3',adopted?'正在展示人工采用版本 · '+adopted.actor:'人工修改：保存草稿与确认采用分开'));
 const switcher=node('button','切回模型原结果编辑');switcher.onclick=()=>fill(record.result||{});root.append(switcher);
 for(const [id,label] of [['editDescription','描述'],['editFacts','关键事实（每行一项）'],['editCode','代码正文（保持换行缩进）']]){root.append(node('label',label));const t=node('textarea');t.id=id;root.append(t);}
 function fill(s){$('editDescription').value=s.description||'';$('editFacts').value=(s.keyFacts||[]).join('\n');$('editCode').value=s.codeText??s.code?.text??'';}fill(source);
 const save=node('button','保存修改（新草稿）');save.id='saveHuman';save.onclick=async()=>{await api('/api/human/save',{...binding(),description:$('editDescription').value,keyFacts:$('editFacts').value.split('\n').filter(Boolean),codeText:$('editCode').value||null});show('草稿已保存，尚未采用。');await openResult(key);};root.append(save);
 const list=node('select');list.id='humanVersions';for(const v of detail.human.versions){const o=node('option',v.createdAt+' · '+v.actor+' · 草稿 '+v.revisionId.slice(0,8));o.value=v.revisionId;list.append(o);}if(detail.human.versions.length)list.value=detail.human.versions.at(-1).revisionId;root.append(list);
 const adopt=node('button','确认采用已保存版本');adopt.id='adoptHuman';adopt.disabled=!detail.human.versions.length;adopt.onclick=async()=>{await api('/api/human/adopt',{...binding(),humanRevisionId:list.value});show('已采用独立人工版本，模型原结果未变。');await openResult(key);};root.append(adopt);
 root.append(node('pre','人工历史与采用事件：'+readable(detail.human)));
}
async function tasks(){const rows=await api('/api/tasks');$('tasks').replaceChildren();for(const t of rows.reverse()){const row=node('div',undefined,'notice');row.append(node('span',t.operation+' · '+t.state+' · '+t.taskId.slice(0,8)));const view=node('button','查看状态');view.onclick=async()=>{show(readable(await api('/api/task/'+t.taskId)));};row.append(view);if(t.operation==='enrichment'&&!['queued','running'].includes(t.state)){const b=node('button','离线汇总恢复（0调用）');b.onclick=async()=>{await api('/api/recover',{taskId:t.taskId});await tasks();show('仅恢复已存证据，没有请求模型。');};row.append(b);}row.append(node('pre',readable({budget:t.callBudget,items:t.items,error:t.error})));$('tasks').append(row);}}
function handle(fn){return async(...args)=>{try{await fn(...args);}catch(e){show('操作未完成：'+e.message);}};}
window.addEventListener('unhandledrejection',e=>{e.preventDefault();show('操作未完成：'+(e.reason?.message||'请查看任务错误记录'));});
window.addEventListener('DOMContentLoaded',handle(async()=>{const b=await api('/api/bootstrap');token=b.csrfToken;$('keyStatus').textContent='API凭据：'+(b.keyConfigured?'已配置':'未配置');$('actor').textContent=b.actor==='codex_test'?'功能验证模式 · 非用户验收':'';
 $('upload').onchange=handle(()=>upload($('upload').files));$('updateUpload').onchange=handle(()=>upload($('updateUpload').files,docId));$('drop').ondragover=e=>e.preventDefault();$('drop').ondrop=handle(e=>{e.preventDefault();return upload(e.dataTransfer.files);});$('search').oninput=handle(documents);$('revision').onchange=handle(()=>openDoc(docId,$('revision').value));$('enrich').onclick=handle(()=>enrich(false));$('rerun').onclick=handle(()=>enrich(true));
 await documents();await tasks();const prior=sessionStorage.getItem('documentSelection');if(prior){const p=JSON.parse(prior);await openDoc(p.id,p.rev);}show('本地工作台就绪；刷新和查看不会触发模型调用。');}));

export function validateGraph(g){
 if(!g||!Array.isArray(g.nodes)||!Array.isArray(g.edges)||!g.git_sha)throw Error('Invalid graph');
 const nodes=new Map();for(const n of g.nodes){if(!n.id||nodes.has(n.id))throw Error('Duplicate node');nodes.set(n.id,n);}
 const edges=new Map();for(const e of g.edges){if(edges.has(e.id)||!nodes.has(e.source)||!nodes.has(e.target)||!['forward','both'].includes(e.direction))throw Error('Invalid edge');edges.set(e.id,e);}
 for(const n of nodes.values()){let cur=n;const seen=new Set();while(cur.parent_id){if(seen.has(cur.id)||!nodes.has(cur.parent_id))throw Error('Invalid hierarchy');seen.add(cur.id);cur=nodes.get(cur.parent_id);}}
 const children=new Map(),incoming=new Map(),outgoing=new Map();for(const n of nodes.values()){if(!children.has(n.parent_id))children.set(n.parent_id,[]);children.get(n.parent_id).push(n.id);incoming.set(n.id,[]);outgoing.set(n.id,[]);}
 for(const e of edges.values()){outgoing.get(e.source).push(e);incoming.get(e.target).push(e);if(e.direction==='both'){outgoing.get(e.target).push({...e,target:e.source,source:e.target});incoming.get(e.source).push(e);}}
 return {g,nodes,edges,children,incoming,outgoing};
}
export function visibleNodes(m,expanded,full=false){return [...m.nodes.values()].filter(n=>{if(full)return true;let p=n.parent_id;while(p){if(!expanded.has(p))return false;p=m.nodes.get(p).parent_id;}return true;});}
export function search(m,q){q=q.toLocaleLowerCase();return [...m.nodes.values()].filter(n=>(n.name+' '+n.description).toLocaleLowerCase().includes(q));}
export function selectionRelations(m,id,dir='both',transitive=false,types=null){const nodeIds=new Set([id]),edgeIds=new Set(),todo=[id];for(let cursor=0;cursor<todo.length;cursor++){const cur=todo[cursor];const edges=[...(dir!=='in'?m.outgoing.get(cur)||[]:[]),...(dir!=='out'?m.incoming.get(cur)||[]:[])];for(const e of edges){if(types&&!types.has(e.type))continue;edgeIds.add(e.id);const other=e.source===cur?e.target:e.source;if(!nodeIds.has(other)){nodeIds.add(other);if(transitive)todo.push(other);}}}return {nodeIds,edgeIds};}
export function dependencies(m,id,dir='both',transitive=false,types=null){return selectionRelations(m,id,dir,transitive,types).nodeIds;}
export function route(m,a,b,types=new Set(['import','static_call']),{edgeAllowed=()=>true,scopeIds=null}={}){if(!m.nodes.has(a)||!m.nodes.has(b)||scopeIds&&(!scopeIds.has(a)||!scopeIds.has(b)))return null;const todo=[a],prev=new Map([[a,null]]);for(let cursor=0;cursor<todo.length;cursor++){const cur=todo[cursor];if(cur===b){const path=[];for(let p=b;prev.get(p);p=prev.get(p).from)path.unshift(prev.get(p).edge);return path;}for(const e of m.outgoing.get(cur)||[]){if(!types.has(e.type)||!edgeAllowed(m.edges.get(e.id))||scopeIds&&!scopeIds.has(e.target)||prev.has(e.target))continue;prev.set(e.target,{from:cur,edge:e.id});todo.push(e.target);}}return null;}
const safeText=v=>String(v??'').slice(0,4000).replace(/(?:Bearer\s+)[\w.\-]+/gi,'Bearer [REDACTED]').replace(/\b(?:sk-|AIza)[\w\-]+/g,'[REDACTED]').replace(/((?:password|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;]+/gi,'$1[REDACTED]');
// Diagnostic imports never display arguments, command strings or arbitrary payloads.
export function importTrace(raw,m){
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw Error('Expected JSON object');
 const task=raw.task&&typeof raw.task==='object'?raw.task:raw;
 const normalized=Array.isArray(raw.events);
 let rows=normalized?raw.events:(Array.isArray(task.diagnostic_trace)?task.diagnostic_trace:Array.isArray(task.commands)?task.commands:null);
 if(!rows||rows.length>10000)throw Error('Expected events, diagnostic_trace or commands; limit 10000');
 const traceId=safeText(raw.trace_id||task.task_id||task.id||'local-import');const ids=new Set();let previousSeq=-Infinity,previousTime=-Infinity;
 const events=rows.map((r,i)=>{
  if(!r||typeof r!=='object'||Array.isArray(r))throw Error('Invalid event');
  const sequence=r.sequence??i;if(!Number.isFinite(sequence)||sequence<previousSeq)throw Error('Unordered sequence');previousSeq=sequence;
  const time=r.elapsed_ms??(typeof r.timestamp==='number'?r.timestamp:r.timestamp?Date.parse(r.timestamp):null);if(time!==null&&(!Number.isFinite(time)||time<previousTime))throw Error('Invalid or unordered time');if(time!==null)previousTime=time;
  const id=safeText(r.id||`event:${i}`);if(ids.has(id))throw Error('Duplicate event ID');ids.add(id);
  // Existing journals are evidenced at their producer; no assumed Worker→UI route.
  let component=r.component_id;if(!normalized&&!component)component='module:server/run_journal.py';
  if(!m.nodes.has(component))component=null;
  const event={id,sequence,timestamp:time,component_id:component,event_type:safeText(r.event_type||r.event||r.tool_name||r.action||'journal'),status:safeText(r.status||'unknown'),task_id:safeText(r.task_id||task.task_id||traceId),step_id:safeText(r.step_id),device:safeText(r.device_id),evidence_reference:safeText(r.evidence_reference||`local:${traceId}#${i}`),related_edge_id:null,gap:component?null:'Компонент отсутствует в снимке',result:{}};
  const result=r.result;if(result&&typeof result==='object'){for(const key of ['status','returncode','error_code','representation'])if(['string','number','boolean'].includes(typeof result[key]))event.result[key]=safeText(result[key]);if(Array.isArray(result.artifacts))event.result.artifact_count=result.artifacts.length;}
  const e=m.edges.get(r.related_edge_id);const prev=i?rows[i-1]:null;
  const sameVersion=raw.architecture_version===m.g.git_sha;
  if(normalized&&sameVersion&&e&&e.confidence==='confirmed'&&prev&&((e.source===prev.component_id&&e.target===component)||(e.direction==='both'&&e.target===prev.component_id&&e.source===component)))event.related_edge_id=e.id;
  else if(i)event.gap=event.gap||'Переход не подтверждён событием и ребром текущего снимка';
  return event;
 });
 // Reports/presentation snapshots are append-only evidence, never guessed transitions.
 if(!normalized){for(const [key,path] of [['worker_report','server/worker_reports.py'],['presentation','server/response_presentation.py']]){const value=task[key];if(value&&typeof value==='object')events.push({id:`snapshot:${key}`,sequence:events.length,timestamp:null,component_id:m.nodes.has('module:'+path)?'module:'+path:null,event_type:key,status:safeText(value.status||'snapshot'),task_id:traceId,step_id:'',evidence_reference:`local:${traceId}#${key}`,related_edge_id:null,gap:'Снимок без подтверждённого времени и перехода',result:{status:safeText(value.status),artifact_count:Array.isArray(value.artifacts)?value.artifacts.length:0,presentation_type:safeText(value.type||value.kind||'')}});}}
 return {trace_id:traceId,architecture_version:safeText(raw.architecture_version||'unknown'),source:raw.source==='DEMO / SYNTHETIC'?'DEMO / SYNTHETIC':'LOCAL READ-ONLY',status:safeText(raw.status||'unknown'),events};
}
export function pulseEdge(event,m){const edge=event?.related_edge_id?m.edges.get(event.related_edge_id):null;return edge?.confidence==='confirmed'&&edge.runtime_eligible!==false?edge:null;}

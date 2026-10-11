// Topology signals, never verdicts about runtime usage, failure or redundancy.
export const STATIC_TYPES=['import','static_call'];
export const SEMANTIC_TYPES=['implements','depends_on'];
export function confirmedRelationship(edge){
 return edge.evidence_kind!=='inferred'&&edge.confidence!=='inferred'&&edge.evidence_kind!=='runtime_observed'&&['confirmed','confirmed_source','verified_architectural_mapping','static_dependency','logical_taxonomy'].includes(edge.confidence||edge.evidence_kind);
}
export function subsystemOf(model,id){let node=model.nodes.get(id);while(node?.parent_id&&node.type!=='subsystem')node=model.nodes.get(node.parent_id);return node?.type==='subsystem'?node:null;}
export function analyzeArchitecture(model,{types=STATIC_TYPES,scopeIds=null,scopeLabel='Весь снимок',layer='all',projectionPolicy='deduplicate',includeInferred=false}={}){
 const selectedTypes=new Set(types),scope=scopeIds?new Set(scopeIds):new Set(model.nodes.keys());
 const records=new Map();for(const id of scope){const n=model.nodes.get(id);if(!n||layer!=='all'&&n.layer!==layer)continue;const group=subsystemOf(model,id);records.set(id,{id,name:n.name,type:n.type,layer:n.layer||'unknown',subsystem_id:group?.id||null,subsystem:group?.name||'IRU / вне подсистемы',confidence:n.evidence_kind||n.confidence||'unknown',inDegree:0,outDegree:0,totalDegree:0,uniqueNeighbors:0,edgeTypes:[],edgeIds:[],references:[],neighborIds:new Set()});}
 let candidates=[...model.edges.values()].filter(e=>selectedTypes.has(e.type)&&records.has(e.source)&&records.has(e.target)&&e.evidence_kind!=='runtime_observed'&&(includeInferred||e.confidence!=='inferred'&&e.evidence_kind!=='inferred'));
 const candidateIds=new Set(candidates.map(e=>e.id));const suppressed=[];
 if(projectionPolicy==='deduplicate')candidates=candidates.filter(e=>{if(e.type!=='depends_on')return true;const backedByIncluded=(e.evidence||[]).some(r=>r.source_edge_id&&candidateIds.has(r.source_edge_id));if(backedByIncluded)suppressed.push(e.id);return !backedByIncluded;});
 else if(projectionPolicy!=='raw')throw Error('Unknown projection policy');
 for(const e of candidates){const source=records.get(e.source),target=records.get(e.target);source.outDegree++;target.inDegree++;if(e.direction==='both'){source.inDegree++;target.outDegree++;}for(const [record,other] of [[source,e.target],[target,e.source]]){record.neighborIds.add(other);record.edgeIds.push(e.id);record.edgeTypes.push(e.type);record.references.push(...e.evidence||[]);}}
 for(const r of records.values()){r.totalDegree=r.inDegree+r.outDegree;r.uniqueNeighbors=r.neighborIds.size;r.edgeTypes=[...new Set(r.edgeTypes)].sort();r.edgeIds=[...new Set(r.edgeIds)];r.references=[...new Map(r.references.map(ref=>[[ref.path,ref.line,ref.symbol,ref.source_edge_id].join('|'),ref])).values()];}
 const categories={orphans:[],deadEnds:[],isolated:[]};for(const r of records.values()){if(!r.inDegree&&!r.outDegree)categories.isolated.push(r);else if(!r.inDegree)categories.orphans.push(r);else if(!r.outDegree)categories.deadEnds.push(r);}
 return {records,categories,edgeIds:new Set(candidates.map(e=>e.id)),metadata:{scopeLabel,layer,nodeCount:records.size,edgeCount:candidates.length,selectedTypes:[...selectedTypes].sort(),usedTypes:[...new Set(candidates.map(e=>e.type))].sort(),projectionPolicy,suppressedDerivedEdges:suppressed.length,includeInferred,runtimeObservedIncluded:false}};
}
export function rankedRecords(records,metric='totalDegree'){
 if(!['inDegree','outDegree','totalDegree','uniqueNeighbors','name'].includes(metric))throw Error('Unknown degree metric');
 return [...records].sort((a,b)=>metric==='name'?a.name.localeCompare(b.name):b[metric]-a[metric]||b.totalDegree-a.totalDegree||a.id.localeCompare(b.id));
}
export function anomalyCategories(analysis,{metric='totalDegree',threshold=20,topN=10}={}){
 if(!Number.isFinite(threshold)||threshold<1||!Number.isInteger(topN)||topN<1)throw Error('Invalid center settings');
 const centers=rankedRecords([...analysis.records.values()].filter(r=>r[metric]>=threshold),metric);
 return {...analysis.categories,centers:centers.slice(0,topN),centerCount:centers.length};
}

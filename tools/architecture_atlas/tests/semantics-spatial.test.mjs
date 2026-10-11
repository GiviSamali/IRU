import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import {validateGraph,visibleNodes,selectionRelations,route,importTrace,pulseEdge} from '../src/model.mjs';
import {layout,region,fitDistance} from '../src/spatial.mjs';
const graph=JSON.parse(await readFile(new URL('../data/architecture.json',import.meta.url)));
const baseline=JSON.parse(await readFile(new URL('../data/v0.1-inventory.json',import.meta.url)));
const model=validateGraph(graph);

test('v0.1 source identities and exact static edge contracts survive',()=>{
 for(const id of baseline.node_ids)assert.ok(model.nodes.has(id),id);
 for(const expected of baseline.static_edges){const current=model.edges.get(expected.id);assert.ok(current,expected.id);for(const key of Object.keys(expected))assert.equal(current[key],expected[key]);}
 assert.equal([...model.nodes.values()].filter(n=>n.type==='subsystem').length,7);
 assert.equal(model.g.schema_version,2);
});
test('reviewed mechanisms connect unique physical sources with evidence and multiple roles',()=>{
 const mechanisms=[...model.nodes.values()].filter(n=>n.type==='mechanism');assert.ok(mechanisms.length>20);
 for(const n of mechanisms){assert.ok(n.responsibility);assert.ok(n.references.some(r=>r.path&&r.line&&r.git_sha===graph.git_sha));assert.ok(Array.isArray(n.inputs));assert.ok(model.incoming.get(n.id).some(e=>e.type==='implements'));}
 const shared=model.nodes.get('module:server/task_runtime.py');assert.ok(shared.architecture_roles.length>=3);
 assert.equal([...model.nodes.values()].filter(n=>n.id===shared.id).length,1);
 for(const e of graph.edges.filter(e=>e.type==='implements')){assert.equal(e.evidence_kind,'verified_architectural_mapping');assert.ok(e.evidence.length);assert.equal(e.runtime_eligible,false);}
 const unmapped=[...model.nodes.values()].filter(n=>n.mapping_status==='inferred');assert.ok(unmapped.length);assert.ok(unmapped.every(n=>model.nodes.get(n.parent_id).type==='implementation_group'));
});
test('navigation reaches exact central definitions through semantic hierarchy',()=>{
 for(const id of ['code:server/controller_non_pipeline.py:process_non_pipeline_command','code:server/worker_reports.py:build_worker_report','code:server/orchestrator.py:decide']){
  const ancestors=[];for(let n=model.nodes.get(id);n;n=n.parent_id?model.nodes.get(n.parent_id):null)ancestors.push(n.type);
  assert.ok(ancestors.includes('mechanism'));assert.ok(ancestors.includes('component'));assert.equal(ancestors.at(-1),'system');
 }
});
test('stable 3D layout independent of graph ordering and expansion',()=>{
 const a=layout(model),reordered=validateGraph({...graph,nodes:[...graph.nodes].reverse(),edges:[...graph.edges].reverse()}),b=layout(reordered);
 assert.deepEqual(a.get('iru'),[0,0,0]);for(const [id,coords] of a){assert.deepEqual(coords,b.get(id));assert.ok(coords.every(Number.isFinite));}
 visibleNodes(model,new Set(['iru','group:2']));assert.deepEqual([...layout(model)],[...a]);
 const groups=[...model.nodes.values()].filter(n=>n.type==='subsystem').map(n=>a.get(n.id));
 for(let i=0;i<groups.length;i++)for(let j=i+1;j<groups.length;j++)assert.ok(Math.hypot(...groups[i].map((v,k)=>v-groups[j][k]))>170);
 assert.ok(Math.max(...groups.map(p=>p[1]))-Math.min(...groups.map(p=>p[1]))>250);
 assert.ok(Math.max(...groups.map(p=>p[2]))-Math.min(...groups.map(p=>p[2]))>250);
 assert.ok(region(model,a,'iru').radius>190);assert.ok(fitDistance(200,50,.6)>fitDistance(200,50,1));
});
test('exact edge highlighting excludes links between unrelated neighbors',()=>{
 const fixture=validateGraph({git_sha:'x',nodes:['a','b','c'].map(id=>({id,name:id,parent_id:null})),edges:[['ab','a','b'],['ac','a','c'],['bc','b','c']].map(([id,source,target])=>({id,source,target,direction:'forward',type:'uses'}))});
 assert.deepEqual([...selectionRelations(fixture,'a','out',false,new Set(['uses'])).edgeIds],['ab','ac']);
 assert.deepEqual([...selectionRelations(fixture,'a','in',false).edgeIds],[]);
 assert.ok(selectionRelations(fixture,'a','out',true).edgeIds.has('bc'));
 assert.equal(selectionRelations(fixture,'a','out',false,new Set(['imports'])).edgeIds.size,0);
 assert.equal(route(fixture,'c','a',new Set(['uses'])),null);
});
test('full graph, collapsed graph and existing execution formats coexist',()=>{
 assert.equal(visibleNodes(model,new Set(),true).length,graph.nodes.length);
 assert.equal(visibleNodes(model,new Set()).length,1);
 const staticEdge=graph.edges.find(e=>e.type==='static_call');
 const trace={architecture_version:graph.git_sha,events:[{component_id:staticEdge.source},{component_id:staticEdge.target,related_edge_id:staticEdge.id}]};
 assert.equal(pulseEdge(importTrace(trace,model).events[1],model).id,staticEdge.id);
 trace.architecture_version=baseline.git_sha;assert.equal(pulseEdge(importTrace(trace,model).events[1],model),null);
 const semantic=graph.edges.find(e=>e.type==='implements');
 trace.architecture_version=graph.git_sha;trace.events=[{component_id:semantic.source},{component_id:semantic.target,related_edge_id:semantic.id}];assert.equal(pulseEdge(importTrace(trace,model).events[1],model),null);
 assert.ok(importTrace({commands:[{tool_name:'execute_cmd',step_id:'s'}]},model).events.length);
});
test('current snapshot CPU budget for layout, indexing and highlights',()=>{
 const started=performance.now();const m=validateGraph(graph);layout(m);for(const n of graph.nodes.filter(n=>n.type==='component'))selectionRelations(m,n.id,'both',true,new Set(['depends_on']));
 assert.ok(performance.now()-started<2500,'CPU budget exceeded (2.5 s); GPU/frame rate requires separate owner check');
});

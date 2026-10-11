// Computes requested snapshot findings, not a test runner. Does not change the graph.
import {readFile,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {validateGraph} from '../src/model.mjs';
import {analyzeArchitecture,anomalyCategories,STATIC_TYPES,SEMANTIC_TYPES} from '../src/diagnostics.mjs';
import {shortestArchitecturePath} from '../src/interactions.mjs';
const input=process.argv[2]||new URL('../data/architecture.json',import.meta.url);
const output=process.argv[3]||new URL('../data/diagnostic-summary.json',import.meta.url);
const m=validateGraph(JSON.parse(await readFile(input)));
let head=null;try{head=execFileSync('git',['rev-parse','HEAD'],{cwd:process.cwd(),encoding:'utf8'}).trim();}catch{}
const summary={repository_head:head,snapshot_git_sha:m.g.git_sha,snapshot_id:m.g.snapshot_id,model_unchanged:true,profiles:{}};
for(const [name,types] of [['static',STATIC_TYPES],['semantic',SEMANTIC_TYPES]]){
 const analysis=analyzeArchitecture(m,{types});const categories=anomalyCategories(analysis,{threshold:20,metric:'totalDegree',topN:10});
 summary.profiles[name]={metadata:analysis.metadata,orphans:categories.orphans.length,dead_ends:categories.deadEnds.length,isolated:categories.isolated.length,centers_above_threshold:categories.centerCount,threshold:20,top10:categories.centers.map(r=>({id:r.id,name:r.name,subsystem:r.subsystem,type:r.type,confidence:r.confidence,in_degree:r.inDegree,out_degree:r.outDegree,total_degree:r.totalDegree,unique_neighbors:r.uniqueNeighbors,edge_types:r.edgeTypes,evidence:r.references.slice(0,8)}))};
}
summary.example_path=shortestArchitecturePath(m,'code:server/orchestrator.py:run_turn','code:server/worker_reports.py:build_worker_report',STATIC_TYPES);
await writeFile(output,JSON.stringify(summary,null,2));
console.log(JSON.stringify({output:typeof output==='string'?resolve(output):output.pathname,profiles:Object.fromEntries(Object.entries(summary.profiles).map(([k,v])=>[k,{orphans:v.orphans,dead_ends:v.dead_ends,isolated:v.isolated,top10:v.top10.map(r=>({name:r.name,in:r.in_degree,out:r.out_degree,total:r.total_degree,neighbors:r.unique_neighbors}))}])),path:summary.example_path.status,steps:summary.example_path.steps.map(s=>({source:s.source,target:s.target,type:s.type,evidence:s.evidence}))},null,2));

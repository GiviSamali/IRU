"""Build topology evidence for the curated critical-node review; no runtime inference."""
import json,sys
from collections import defaultdict
from pathlib import Path
sys.setrecursionlimit(10000)
ROOT=Path(__file__).resolve().parents[1]
g=json.loads((ROOT/'data/architecture.json').read_text(encoding='utf-8'))
N={n['id']:n for n in g['nodes']}
def analyze(types):
 edges=[e for e in g['edges'] if e['type'] in types and e.get('confidence')!='inferred' and e.get('evidence_kind')!='inferred']
 adj={id:set() for id in N};rev={id:set() for id in N};und={id:set() for id in N};incoming=defaultdict(int);outgoing=defaultdict(int)
 for e in edges:
  a,b=e['source'],e['target'];adj[a].add(b);rev[b].add(a);und[a].add(b);und[b].add(a);incoming[b]+=1;outgoing[a]+=1
  if e.get('direction')=='both':adj[b].add(a);rev[a].add(b);incoming[a]+=1;outgoing[b]+=1
 # Tarjan SCC and undirected articulation/bridges use unique endpoints, not parallel projections.
 order={};low={};stack=[];active=set();scc=[]
 def visit(v):
  order[v]=low[v]=len(order);stack.append(v);active.add(v)
  for w in adj[v]:
   if w not in order:visit(w);low[v]=min(low[v],low[w])
   elif w in active:low[v]=min(low[v],order[w])
  if low[v]==order[v]:
   block=[]
   while True:
    w=stack.pop();active.remove(w);block.append(w)
    if w==v:break
   scc.append(sorted(block))
 for v in N:
  if v not in order:visit(v)
 disc={};lo={};cuts=set();bridges=[]
 def walk(v,parent=None):
  disc[v]=lo[v]=len(disc);children=0
  for w in und[v]:
   if w==v:continue
   if w not in disc:
    children+=1;walk(w,v);lo[v]=min(lo[v],lo[w])
    if parent is not None and lo[w]>=disc[v]:cuts.add(v)
    if lo[w]>disc[v]:bridges.append([v,w])
   elif w!=parent:lo[v]=min(lo[v],disc[w])
  if parent is None and children>1:cuts.add(v)
 for v in N:
  if v not in disc:walk(v)
 sizes={id:len(block) for block in scc for id in block}
 def reach(id):
  seen={id};todo=[id]
  for v in todo:
   for w in rev[v]:
    if w not in seen:seen.add(w);todo.append(w)
  return len(seen)-1
 metrics={id:{'incoming':incoming[id],'outgoing':outgoing[id],'neighbors':len(und[id]-{id}),'reverse_reachable':reach(id),'articulation':id in cuts,'scc_size':sizes[id]} for id in N}
 top=sorted(N,key=lambda id:(-incoming[id]-outgoing[id],id))[:15]
 return {'types':types,'edge_count':len(edges),'connected_nodes':sum(bool(und[id]) for id in N),'articulation_count':len(cuts),'bridges_count':len(bridges),'cycles':[block for block in scc if len(block)>1],'metrics':metrics,'top':top}
result={'schema_version':1,'architecture_git_sha':g['git_sha'],'static':analyze(['import','static_call']),'semantic':analyze(['depends_on'])}
(ROOT/'data/connectivity-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
for kind in ['static','semantic']:
 a=result[kind];print(kind,a['edge_count'],'edges',a['articulation_count'],'cuts',len(a['cycles']),'cyclic groups')

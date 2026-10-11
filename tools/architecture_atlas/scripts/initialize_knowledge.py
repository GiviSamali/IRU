"""Create first authored catalogs. Refuse all overwrites, including partial files."""
import importlib.util
import json
import subprocess
from pathlib import Path
from knowledge import architecture_binding,reviewed_bindings,build

def initialize(base,repo):
    directory=base/'knowledge';targets=[directory/'nodes.json',directory/'relationships.json']
    if any(path.exists() for path in targets):raise ValueError('Human catalogs already exist; initial authoring cannot overwrite them')
    spec=importlib.util.spec_from_file_location('authored_knowledge',directory/'authoring.py');authored=importlib.util.module_from_spec(spec);spec.loader.exec_module(authored)
    graph=json.loads((base/'data/architecture.json').read_text(encoding='utf-8-sig'));nodes={n['id']:n for n in graph['nodes']};edges={e['id']:e for e in graph['edges']}
    # Initial prose was reviewed against these source implementations. A new Git
    # SHA alone is harmless; changed bound files require a new human review.
    paths=sorted({r['path'] for n in graph['nodes'] if n['id'] in authored.NODE_TEXT for r in n.get('references',[]) if r.get('path')})
    changed=subprocess.run(['git','diff','--quiet','ee3cc4971e5b88f9853e36cef38e7e986f7fd858','--',*paths],cwd=repo)
    if changed.returncode!=0:raise ValueError('Initial prose needs source review before seeding this checkout')
    node_records={};relationship_records={}
    for pair,prose in authored.RELATION_TEXT.items():
        source,target=('component:'+name for name in pair);eid=f'depends_on:{source}->{target}'
        if eid not in edges:raise ValueError('Authored relationship not present in graph: '+eid)
        e=edges[eid];why,data,conditions,failure=prose
        relationship_records[eid]={'id':eid,'purpose':why,'data_or_signals':data,'conditions':conditions,'failure_behavior':failure,'interaction_kind':'aggregated_architectural_dependency','unknown_contracts':['Не доказывает участие в конкретном запуске; полная динамическая последовательность не установлена.'],'reviewed':True,'reviewed_against':graph['git_sha'],'architecture_binding_digest':architecture_binding(e),'bindings':reviewed_bindings(repo,e.get('evidence',[])),'related_node_ids':[source,target]}
    for identifier,prose in authored.NODE_TEXT.items():
        if identifier not in nodes:raise ValueError('Authored node not present in graph: '+identifier)
        n=nodes[identifier];what,why,how=prose
        interactions=[eid for eid in relationship_records if identifier in (edges[eid]['source'],edges[eid]['target'])]
        outgoing=[e['target'] for e in graph['edges'] if e['source']==identifier and e['type']=='depends_on'];incoming=[e['source'] for e in graph['edges'] if e['target']==identifier and e['type']=='depends_on']
        node_records[identifier]={'id':identifier,'what':what,'why':why,'how':how,'relationship_ids':interactions,'dependencies':list(dict.fromkeys(outgoing)),'dependents':list(dict.fromkeys(incoming)),'limitations':list(n.get('unknowns',[]))+list(n.get('trust_boundaries',[])),'unknown_contracts':['Полный контракт отказов и все косвенные runtime-участники не установлены.'],'implementation_references':n.get('references',[]),'reviewed':True,'reviewed_against':graph['git_sha'],'architecture_binding_digest':architecture_binding(n),'bindings':reviewed_bindings(repo,n.get('references',[])),'related_node_ids':list(dict.fromkeys(outgoing+incoming))}
    for path,key,records in [(targets[0],'nodes',node_records),(targets[1],'relationships',relationship_records)]:
        with path.open('x',encoding='utf-8') as stream:stream.write(json.dumps({'schema_version':1,key:records},ensure_ascii=False,indent=2))
    result=build(repo,base/'data/architecture.json',directory,base/'data/knowledge-status.json');print(json.dumps(result['summary'],ensure_ascii=False))

if __name__=='__main__':
    base=Path(__file__).resolve().parents[1];initialize(base,base.parents[1])

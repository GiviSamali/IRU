"""Read tracked source only; never import or execute IRU modules."""
import ast
import json
import subprocess
import argparse
from pathlib import Path
from semantics import apply_semantics

GROUPS = ['Взаимодействие с человеком', 'Интеллект и понимание', 'Управление задачами', 'Исполнение и устройства', 'Память и состояние', 'Безопасность и полномочия', 'Контроль результатов и доказательства']
def group(path):
    p = path.lower()
    if any(s in p for s in ('auditor','journal','report','presentation','evidence')): return 6
    if any(s in p for s in ('auth','security','permission','confirmation','trust')): return 5
    if any(s in p for s in ('memory','database','state','context')): return 4
    if p.startswith(('agent/','browser_extension/')) or any(s in p for s in ('device','tool','runtime')): return 3
    if any(s in p for s in ('worker','scheduler','task','goal')): return 2
    if any(s in p for s in ('orchestrator','llm','controller','prompt')): return 1
    return 0

def extract(root):
    sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root,text=True).strip()
    files = subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines()
    nodes = [dict(id='iru',name='IRU',type='system',parent_id=None,level=0,description='Единый архитектурный атлас',confidence='logical',references=[])]
    edges=[]; edge_ids=set(); errors=[]; trees={}; symbols={}; modules={}; symbol_ids=set()
    for i,name in enumerate(GROUPS): nodes.append(dict(id=f'group:{i}',name=name,type='subsystem',parent_id='iru',level=1,description='Логическая классификация, не отдельный сервис',confidence='logical',references=[]))
    def ref(p,n,why): return dict(path=p,symbol=getattr(n,'name',None),line=getattr(n,'lineno',1),end_line=getattr(n,'end_lineno',1),git_sha=sha,explanation=why)
    for p in files:
        if not p.startswith(('server/','agent/','ui/','browser_extension/')) or not p.endswith(('.py','.js','.html')): continue
        mid='module:'+p; modules[p]=mid
        nodes.append(dict(id=mid,name=p,type='module',parent_id=f'group:{group(p)}',level=2,description='Фактический исходный модуль',confidence='confirmed',references=[dict(path=p,line=1,git_sha=sha,explanation='git ls-files')]))
        if not p.endswith('.py'): continue
        try: trees[p]=ast.parse((root/p).read_text(encoding='utf-8-sig'))
        except (SyntaxError,UnicodeError) as exc: errors.append(dict(path=p,error=type(exc).__name__));continue
        def walk(body,parent,qual=''):
            for n in body:
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    q=qual+n.name; sid='code:'+p+':'+q
                    if sid in symbol_ids:
                        errors.append(dict(path=p,error='Duplicate qualified definition',symbol=q,line=n.lineno))
                        continue
                    symbol_ids.add(sid)
                    symbols[(p,q)]=sid
                    kind='class' if isinstance(n,ast.ClassDef) else 'function'
                    tags=[]
                    for decorator in n.decorator_list:
                        expr=decorator.func if isinstance(decorator,ast.Call) else decorator
                        if isinstance(expr,ast.Attribute) and expr.attr in {'get','post','put','delete','patch','websocket','route'}:tags.append('api_endpoint' if expr.attr!='websocket' else 'event_handler')
                    nodes.append(dict(id=sid,name=q,type=kind,kind_tags=tags,parent_id=parent,level=3+q.count('.'),description=ast.get_docstring(n) or 'Определение в исходном коде',confidence='confirmed',references=[ref(p,n,'AST definition')]))
                    walk(n.body,sid,q+'.')
                else:
                    for field in ('body','orelse','finalbody'):
                        v=getattr(n,field,None)
                        if isinstance(v,list): walk(v,parent,qual)
        walk(trees[p].body,mid)
    def edge(a,b,kind,evidence):
        if a==b:return
        key=f'{kind}:{a}->{b}'
        if key not in edge_ids:
            edge_ids.add(key)
            edges.append(dict(id=key,source=a,target=b,type=kind,direction='forward',confidence='confirmed',description='Статическая зависимость; не доказательство runtime',evidence=[evidence]))
    def module_path(mod,p):
        target=mod.replace('.','/')+'.py'
        if target in modules:return target
        local=p.rsplit('/',1)[0]+'/'+target
        return local if local in modules else target
    for p,tree in trees.items():
        package=p.rsplit('/',1)[0].replace('/','.')
        aliases={}
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):
                base=package.split('.')
                prefix='.'.join(base[:len(base)-n.level+1]) if n.level else ''
                mod='.'.join(x for x in (prefix,n.module or '') if x)
                for a in n.names: aliases[a.asname or a.name]=(mod,a.name)
                target=module_path(mod,p)
                if target in modules: edge(modules[p],modules[target],'import',ref(p,n,'AST ImportFrom'))
            elif isinstance(n,ast.Import):
                for a in n.names:
                    aliases[a.asname or a.name]=(a.name,None)
                    target=module_path(a.name,p)
                    if target in modules:edge(modules[p],modules[target],'import',ref(p,n,'AST Import'))
        def calls(body,qual=''):
            for n in body:
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    q=qual+n.name; caller=symbols.get((p,q))
                    def own_walk(node):
                        yield node
                        for child in ast.iter_child_nodes(node):
                            if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):continue
                            yield from own_walk(child)
                    for c in own_walk(n):
                        if not isinstance(c,ast.Call):continue
                        target=None
                        if isinstance(c.func,ast.Name):
                            name=c.func.id; target=symbols.get((p,name))
                            if name in aliases:
                                mod,sym=aliases[name];target=symbols.get((module_path(mod,p),sym))
                        elif isinstance(c.func,ast.Attribute) and isinstance(c.func.value,ast.Name) and c.func.value.id in aliases:
                            mod,_=aliases[c.func.value.id]; target=symbols.get((module_path(mod,p),c.func.attr))
                        if caller and target:edge(caller,target,'static_call',ref(p,c,'AST named call; dynamic dispatch not resolved'))
                    calls(n.body,q+'.')
        calls(tree.body)
    return apply_semantics(dict(schema_version=1,git_sha=sha,nodes=nodes,edges=edges,coverage=dict(source_files=len(modules),python_files=len(trees),entities=len(symbols),nodes=len(nodes),static_edges=len(edges),runtime_edges=0,parse_errors=errors)))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3]);ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/architecture.json');args=ap.parse_args()
    model=extract(args.repo);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(model,ensure_ascii=False),encoding='utf-8');print(json.dumps(model['coverage'],ensure_ascii=False))

"""Persistent human knowledge. Building status never writes the human catalogs."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def source_binding(repo,reference):
    path=reference['path'];target=(repo/path).resolve()
    if not target.is_relative_to(repo.resolve()):raise ValueError('Source outside repository')
    text=target.read_text(encoding='utf-8-sig');symbol=reference.get('symbol');line=reference.get('line',1)
    value=text.replace('\r\n','\n');algorithm='module-text-v1';resolved=symbol
    import_signature=None
    if path.endswith('.py'):
        tree=ast.parse(text);entities={}
        def visit(node,scope=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    q=scope+child.name;entities[q]=child;visit(child,q+'.')
                else:visit(child,scope)
        visit(tree)
        if reference.get('source_scope')=='import':
            matches=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom)) and ast.dump(n,include_attributes=False)==reference.get('import_signature')]
            if not matches:raise ValueError('Bound import changed or disappeared')
            chosen=matches[0];import_signature=reference['import_signature']
        elif symbol:
            if symbol not in entities:
                candidates=[(q,n) for q,n in entities.items() if q.rsplit('.',1)[-1]==symbol and n.lineno==line]
                if len(candidates)!=1:raise ValueError('Source symbol missing/ambiguous: '+path+':'+symbol)
                resolved,chosen=candidates[0]
            else:chosen=entities[symbol]
        elif reference.get('source_scope')=='module' or line==1:chosen=tree
        else:
            matches=[(q,n) for q,n in entities.items() if n.lineno<=line<=n.end_lineno]
            if matches:resolved,chosen=min(matches,key=lambda item:item[1].end_lineno-item[1].lineno)
            else:
                imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom)) and n.lineno<=line<=n.end_lineno]
                if not imports:raise ValueError('No source entity/import at evidence line: '+path+':'+str(line))
                chosen=imports[0];import_signature=ast.dump(chosen,include_attributes=False)
        value=ast.dump(chosen,include_attributes=False);algorithm='python-ast-v1'
    result={'path':path,'symbol':resolved,'source_scope':'import' if import_signature else 'module' if not resolved else 'entity','algorithm':algorithm,'digest':digest(value)}
    if import_signature:result['import_signature']=import_signature
    return result

def architecture_binding(entity):
    fields=('type','parent_id','responsibility','inputs','outputs','trust_boundaries','architecture_roles') if 'parent_id' in entity else ('source','target','type','direction','evidence_kind')
    value={key:entity.get(key) for key in fields}
    references=entity.get('references' if 'parent_id' in entity else 'evidence',[])
    value['source_anchors']=sorted({(r.get('path') or '',r.get('symbol') or '',r.get('source_edge_id') or '') for r in references})
    return digest(value)

def reviewed_bindings(repo,references):
    bindings={}
    for ref in references:
        if not ref.get('path'):continue
        binding=source_binding(repo,ref);bindings[(binding['path'],binding['symbol'],binding['source_scope'],binding.get('import_signature'))]=binding
    return list(bindings.values())

def load_catalog(path,key):
    if not path.exists():return {'schema_version':1,key:{}}
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if value.get('schema_version')!=1 or not isinstance(value.get(key),dict):raise ValueError('Invalid knowledge catalog')
    for identifier,record in value[key].items():
        if not isinstance(record,dict) or record.get('id')!=identifier:raise ValueError('Knowledge ID mismatch')
    return value

def merge_knowledge(model,nodes_catalog,relationships_catalog,repo):
    result={'schema_version':1,'architecture_git_sha':model['git_sha'],'nodes':{},'relationships':{},'issues':[],'summary':{}}
    cache={};node_ids={n['id'] for n in model['nodes']};edge_ids={e['id'] for e in model['edges']}
    for category,entities,catalog in [('nodes',model['nodes'],nodes_catalog),('relationships',model['edges'],relationships_catalog)]:
        current={entity['id']:entity for entity in entities};records=catalog[category]
        for identifier,entity in current.items():
            record=records.get(identifier)
            if not record:
                result[category][identifier]={'status':'missing_description'};continue
            reasons=[]
            if record.get('architecture_binding_digest')!=architecture_binding(entity):reasons.append('Архитектурная привязка или контракт изменились')
            for old in record.get('bindings',[]):
                key=(old['path'],old.get('symbol'),old.get('source_scope'),old.get('import_signature'))
                try:
                    if key not in cache:cache[key]=source_binding(repo,old)
                    if cache[key]['digest']!=old.get('digest'):reasons.append('Изменилась реализация: '+old['path']+':'+str(old.get('symbol') or 'module'))
                except (OSError,ValueError,SyntaxError) as exc:
                    reasons.append('Потеряна привязка: '+old['path']+':'+str(old.get('symbol') or 'module'));result['issues'].append({'id':identifier,'binding':old,'reason':str(exc)})
            for related in record.get('related_node_ids',[]):
                if related not in node_ids:
                    reasons.append('Отсутствует связанный узел: '+related);result['issues'].append({'id':identifier,'reason':'Related node missing: '+related})
            for related in record.get('relationship_ids',[]):
                if related not in edge_ids:
                    reasons.append('Отсутствует объясняемая связь: '+related);result['issues'].append({'id':identifier,'reason':'Related edge missing: '+related})
            if not record.get('reviewed'):reasons.append('Описание ещё не прошло ручную проверку')
            result[category][identifier]={'status':'review_required' if reasons else 'current','reasons':list(dict.fromkeys(reasons)),'record':record}
        for identifier,record in records.items():
            if identifier not in current:
                result[category][identifier]={'status':'orphaned_record','record':record,'reasons':['ID отсутствует в текущем снимке; текст сохранён, автоматического переноса нет']}
                result['issues'].append({'id':identifier,'reason':'Knowledge record has no current graph entity'})
        counts={}
        for row in result[category].values():counts[row['status']]=counts.get(row['status'],0)+1
        result['summary'][category]=counts
    return result

def build(repo,model_path,knowledge_dir,output):
    if output.resolve().is_relative_to(knowledge_dir.resolve()) or output.resolve()==model_path.resolve():raise ValueError('Generated status must not overwrite human knowledge or architecture')
    model=json.loads(model_path.read_text(encoding='utf-8-sig'))
    nodes=load_catalog(knowledge_dir/'nodes.json','nodes');relations=load_catalog(knowledge_dir/'relationships.json','relationships')
    result=merge_knowledge(model,nodes,relations,repo)
    parsed={};facts={}
    for node in model['nodes']:
        if node.get('layer')!='physical':continue
        reference=next((r for r in node.get('references',[]) if r.get('path')),None)
        if not reference:continue
        path=reference['path'];symbol=node['id'].split(':',2)[2] if node['id'].startswith('code:') else reference.get('symbol')
        if not path.endswith('.py'):facts[node['id']]={'path':path,'kind':'module','signature':None,'docstring':None};continue
        try:
            if path not in parsed:
                target=(repo/path).resolve()
                if not target.is_relative_to(repo.resolve()):raise ValueError('Technical source outside repository')
                tree=ast.parse(target.read_text(encoding='utf-8-sig'));entities={}
                def visit(parent,scope=''):
                    for child in ast.iter_child_nodes(parent):
                        if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                            q=scope+child.name;entities[q]=child;visit(child,q+'.')
                        else:visit(child,scope)
                visit(tree);parsed[path]=(tree,entities)
            tree,entities=parsed[path];entity=entities.get(symbol) if symbol else tree
            if entity is None:continue
            signature=symbol+'('+ast.unparse(entity.args)+')' if isinstance(entity,(ast.FunctionDef,ast.AsyncFunctionDef)) else 'class '+str(symbol) if isinstance(entity,ast.ClassDef) else None
            facts[node['id']]={'path':path,'signature':signature,'docstring':ast.get_docstring(entity),'kind':node['type']}
        except (OSError,SyntaxError,ValueError) as exc:result['issues'].append({'id':node['id'],'reason':'Technical facts unavailable: '+str(exc)})
    result['technical_facts']=facts
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def edit_one(path,key,identifier,patch):
    catalog=load_catalog(path,key)
    if identifier not in catalog[key]:raise ValueError('Unknown knowledge record')
    # Explicit user-authored patch only; no generated prose or automatic re-review.
    if not isinstance(patch,dict) or any(k in patch for k in ('id','bindings','architecture_binding_digest')):raise ValueError('Protected knowledge metadata')
    catalog[key][identifier].update(patch);catalog[key][identifier]['reviewed']=False
    temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,path)

def review_one(path,key,identifier,model,repo):
    catalog=load_catalog(path,key);entity=next((e for e in model['nodes' if key=='nodes' else 'edges'] if e['id']==identifier),None)
    if not entity or identifier not in catalog[key]:raise ValueError('Cannot review absent entity/record')
    record=catalog[key][identifier];record.update(reviewed=True,reviewed_against=model['git_sha'],architecture_binding_digest=architecture_binding(entity),bindings=reviewed_bindings(repo,entity.get('references' if key=='nodes' else 'evidence',[])))
    temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,path)

if __name__=='__main__':
    base=Path(__file__).resolve().parents[1];parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=base.parents[1]);parser.add_argument('--model',type=Path,default=base/'data/architecture.json');parser.add_argument('--knowledge-dir',type=Path,default=base/'knowledge');parser.add_argument('--output',type=Path,default=base/'data/knowledge-status.json')
    parser.add_argument('--edit-kind',choices=['nodes','relationships']);parser.add_argument('--review-kind',choices=['nodes','relationships']);parser.add_argument('--id');parser.add_argument('--patch',type=Path)
    args=parser.parse_args()
    if args.edit_kind:
        if not args.id or not args.patch:parser.error('--edit-kind requires --id and --patch')
        edit_one(args.knowledge_dir/(args.edit_kind+'.json'),args.edit_kind,args.id,json.loads(args.patch.read_text(encoding='utf-8-sig')))
    if args.review_kind:
        if not args.id:parser.error('--review-kind requires --id; use only after human source review')
        review_one(args.knowledge_dir/(args.review_kind+'.json'),args.review_kind,args.id,json.loads(args.model.read_text(encoding='utf-8-sig')),args.repo)
    result=build(args.repo,args.model,args.knowledge_dir,args.output);print(json.dumps(result['summary'],ensure_ascii=False));print('Lost bindings/records:',len(result['issues']))

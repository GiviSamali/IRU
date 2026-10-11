"""Overlay reviewed definitions without deleting source nodes or static edges."""
from semantic_catalog import CATALOG, UNRESOLVED

def apply_semantics(model):
    nodes=model['nodes'];edges=model['edges'];by_id={n['id']:n for n in nodes};edge_ids={e['id'] for e in edges}
    source_nodes=set(by_id);source_edges=set(edge_ids);assignments={};roles={};logical=[]
    def add_edge(source,target,kind,evidence,classification='verified_architectural_mapping'):
        eid=f'{kind}:{source}->{target}'
        if eid in edge_ids:return
        if source not in by_id or target not in by_id:raise ValueError('Semantic reference not found: '+eid)
        edge_ids.add(eid);edges.append(dict(id=eid,source=source,target=target,type=kind,direction='forward',
             confidence=classification,evidence_kind=classification,runtime_eligible=False,
             description='Проверенная функциональная роль' if kind=='implements' else 'Навигационная вложенность' if kind=='contains' else 'Агрегация статической зависимости; не runtime',evidence=evidence))
    for n in nodes:
        if n['type']=='function' and n['parent_id'] and by_id[n['parent_id']]['type']=='class':n['type']='method'
        n['layer']='logical' if n['type'] in {'system','subsystem'} else 'physical'
        n['evidence_kind']='logical_taxonomy' if n['layer']=='logical' else 'confirmed_source'
        n['architecture_roles']=[]
    for e in edges:e['evidence_kind']='static_dependency';e['runtime_eligible']=True
    for definition in CATALOG:
        comp={k:v for k,v in definition.items() if k!='mechanisms'}
        comp.update(layer='logical',confidence='verified_architectural_mapping',evidence_kind='verified_architectural_mapping',references=[],architecture_roles=[],unknowns=['Функциональная группировка; не утверждение о независимом процессе.'])
        if comp['id'] in by_id:raise ValueError('Logical ID collision')
        nodes.append(comp);by_id[comp['id']]=comp;logical.append(comp)
        for definition_m in definition['mechanisms']:
            mech={k:v for k,v in definition_m.items() if k!='bindings'}
            mech.update(parent_id=comp['id'],layer='logical',confidence='verified_architectural_mapping',evidence_kind='verified_architectural_mapping',references=[],architecture_roles=[])
            if mech['id'] in by_id:raise ValueError('Mechanism ID collision')
            nodes.append(mech);by_id[mech['id']]=mech;logical.append(mech)
            for bind in definition_m['bindings']:
                mid='module:'+bind['path']
                if mid not in by_id:raise ValueError('Missing semantic module: '+mid)
                targets=['code:'+bind['path']+':'+s for s in bind['symbols']]
                for target in targets:
                    if target not in by_id:raise ValueError('Missing semantic definition: '+target)
                evidence=[]
                for target in targets or [mid]:
                    for ref in by_id[target]['references']:
                        evidence.append({**ref,'explanation':mech['responsibility']+'; reviewed definition/contract binding'})
                mech['references'].extend(evidence);comp['references'].extend(evidence)
                assignments.setdefault(mid,mech['id'])
                for target in [mid]+targets:
                    add_edge(target,mech['id'],'implements',evidence)
                    by_id[target]['architecture_roles'].append(mech['id'])
                    roles.setdefault(target,set()).add(mech['id'])
    # One physical identity, one navigation parent, any number of functional roles.
    for n in list(nodes):
        if n['type']!='module':continue
        if n['id'] in assignments:n['parent_id']=assignments[n['id']]
        else:
            group_id=n['parent_id'];bucket_id='unmapped:'+group_id
            if bucket_id not in by_id:
                bucket=dict(id=bucket_id,name='Непривязанные реализации',type='implementation_group',parent_id=group_id,layer='physical',description='Эвристический fallback v0.1; функциональная принадлежность не установлена.',confidence='inferred',evidence_kind='inferred',references=[],architecture_roles=[],unknowns=['Требуется ручное установление механизмов.'])
                nodes.append(bucket);by_id[bucket_id]=bucket
            n['parent_id']=bucket_id;n['mapping_status']='inferred'
    # Exact static edges remain intact. Derived architectural dependencies cite them.
    for e in list(edges):
        if e['id'] not in source_edges:continue
        for a in sorted(roles.get(e['source'],set())):
            for b in sorted(roles.get(e['target'],set())):
                if a!=b:
                    proof=[{**ref,'source_edge_id':e['id'],'explanation':'Статическое ребро '+e['id']+' между привязанными реализациями'} for ref in e['evidence']]
                    add_edge(a,b,'depends_on',proof,'static_dependency')
                    ca=by_id[a]['parent_id'];cb=by_id[b]['parent_id']
                    if ca!=cb:
                        add_edge(ca,cb,'depends_on',proof,'static_dependency')
                        ga=by_id[ca]['parent_id'];gb=by_id[cb]['parent_id']
                        if ga!=gb:add_edge(ga,gb,'depends_on',proof,'static_dependency')
    def level(n,seen=None):
        seen=set() if seen is None else seen
        if n['id'] in seen:raise ValueError('Semantic parent cycle')
        if n['parent_id'] is None:return 0
        return 1+level(by_id[n['parent_id']],seen|{n['id']})
    for n in nodes:n['level']=level(n)
    for n in nodes:
        if n['parent_id']:
            refs=n.get('references') or [dict(git_sha=model['git_sha'],explanation='Логическая классификация или AST ownership; не runtime')]
            classification='inferred' if n.get('mapping_status')=='inferred' or n['type']=='implementation_group' else 'confirmed_source' if n['layer']=='physical' and by_id[n['parent_id']]['layer']=='physical' else 'logical_taxonomy' if n['type']=='subsystem' else 'verified_architectural_mapping'
            add_edge(n['parent_id'],n['id'],'contains',refs,classification)
    for n in logical:
        refs={ (r.get('path'),r.get('symbol'),r.get('line')):r for r in n['references'] }
        n['references']=list(refs.values())
    model['schema_version']=2;model['semantic_version']='0.2';model['snapshot_id']=model['git_sha']+':semantics-0.2'
    model['compatibility']=dict(source_id_scheme='v1 unchanged',static_edge_id_scheme='v1 unchanged',trace_version_field='git_sha',semantic_edges_runtime_eligible=False)
    c=model['coverage'];c.update(nodes=len(nodes),edges=len(edges),components=len(CATALOG),mechanisms=len(logical)-len(CATALOG),mapped_modules=len(assignments),unmapped_modules=c['source_files']-len(assignments),semantic_edges=len(edges)-len(source_edges),preserved_source_nodes=len(source_nodes),preserved_static_edges=len(source_edges),unresolved=UNRESOLVED)
    c['limitations']=['JS/HTML modules only; no JS symbol analysis','Unmapped code retains explicit inferred fallback','Dynamic callback/registry/event dependencies unresolved','Static architectural dependencies do not prove execution']
    return model

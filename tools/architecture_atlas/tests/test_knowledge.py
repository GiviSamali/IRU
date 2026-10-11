import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from knowledge import architecture_binding,reviewed_bindings,merge_knowledge,build,edit_one

class KnowledgePersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'module.py';self.source.write_text('def useful(value):\n    return value + 1\n',encoding='utf-8')
        self.node={'id':'code:module.py:useful','type':'function','layer':'physical','parent_id':None,'references':[{'path':'module.py','symbol':'useful','line':1}]}
        self.graph={'git_sha':'first','nodes':[self.node],'edges':[]}
        record={'id':self.node['id'],'what':'Конкретный преобразователь значения.','why':'Сохраняет ожидаемый арифметический контракт.','how':'Возвращает значение после прибавления единицы.','unknown_contracts':['Поведение вызывающего кода при отказе не установлено.'],'reviewed':True,'architecture_binding_digest':architecture_binding(self.node),'bindings':reviewed_bindings(self.root,self.node['references'])}
        self.nodes={'schema_version':1,'nodes':{self.node['id']:record}};self.edges={'schema_version':1,'relationships':{}}
    def merge(self,graph=None):return merge_knowledge(graph or self.graph,self.nodes,self.edges,self.root)
    def test_repeat_and_new_function_preserve_catalog_and_current_text(self):
        original=copy.deepcopy(self.nodes);a=self.merge();b=self.merge();self.assertEqual(a,b);self.assertEqual(self.nodes,original)
        self.source.write_text(self.source.read_text()+'\ndef unrelated():\n    return 42\n',encoding='utf-8')
        graph=copy.deepcopy(self.graph);graph['git_sha']='new-commit';graph['nodes'].append({'id':'code:module.py:unrelated','type':'function','parent_id':None})
        result=self.merge(graph);self.assertEqual(result['nodes'][self.node['id']]['status'],'current');self.assertEqual(result['nodes']['code:module.py:unrelated']['status'],'missing_description');self.assertEqual(self.nodes,original)
    def test_related_source_change_preserves_text_and_marks_review(self):
        self.source.write_text('def useful(value):\n    return value - 1\n',encoding='utf-8');result=self.merge()['nodes'][self.node['id']]
        self.assertEqual(result['status'],'review_required');self.assertEqual(result['record']['what'],self.nodes['nodes'][self.node['id']]['what'])
    def test_deleted_and_ambiguous_rename_never_transfer_or_delete(self):
        graph={'git_sha':'new','nodes':[{'id':'new:a','type':'function','parent_id':None},{'id':'new:b','type':'function','parent_id':None}],'edges':[]};result=self.merge(graph)
        self.assertEqual(result['nodes'][self.node['id']]['status'],'orphaned_record');self.assertEqual(result['nodes']['new:a']['status'],'missing_description');self.assertEqual(result['nodes']['new:b']['status'],'missing_description');self.assertIn(self.node['id'],self.nodes['nodes'])
    def test_normalized_ast_ignores_line_shift_and_unrelated_git_sha(self):
        self.source.write_text('\n\n'+self.source.read_text(),encoding='utf-8');graph=copy.deepcopy(self.graph);graph['git_sha']='changed';self.assertEqual(self.merge(graph)['nodes'][self.node['id']]['status'],'current')
    def test_status_build_never_overwrites_authoritative_files(self):
        directory=self.root/'knowledge';directory.mkdir();np=directory/'nodes.json';rp=directory/'relationships.json';np.write_text(json.dumps(self.nodes),encoding='utf-8');rp.write_text(json.dumps(self.edges),encoding='utf-8');model=self.root/'architecture.json';model.write_text(json.dumps(self.graph),encoding='utf-8');before=(np.read_bytes(),rp.read_bytes())
        build(self.root,model,directory,self.root/'status.json');build(self.root,model,directory,self.root/'status.json');self.assertEqual(before,(np.read_bytes(),rp.read_bytes()))
        with self.assertRaises(ValueError):build(self.root,model,directory,np)
        with self.assertRaises(ValueError):build(self.root,model,directory,model)
    def test_single_record_edit_requires_review_without_changing_other_record(self):
        p=self.root/'nodes.json';catalog=copy.deepcopy(self.nodes);other=copy.deepcopy(next(iter(catalog['nodes'].values())));other['id']='other';catalog['nodes']['other']=other;p.write_text(json.dumps(catalog),encoding='utf-8');edit_one(p,'nodes',self.node['id'],{'why':'Уточнённая человеческая цель.'});value=json.loads(p.read_text());self.assertEqual(value['nodes']['other'],other);self.assertFalse(value['nodes'][self.node['id']]['reviewed'])
    def test_authored_coverage_ids_and_nonduplicated_primary_prose(self):
        base=Path(__file__).resolve().parents[1];g=json.loads((base/'data/architecture.json').read_text(encoding='utf-8-sig'));catalog=json.loads((base/'knowledge/nodes.json').read_text(encoding='utf-8-sig'));ids={n['id'] for n in g['nodes']}
        self.assertEqual(len([k for k in catalog['nodes'] if k.startswith('group:')]),7);self.assertEqual(len([k for k in catalog['nodes'] if k.startswith('component:')]),19);self.assertEqual(len([k for k in catalog['nodes'] if k.startswith('mechanism:')]),34)
        for identifier,r in catalog['nodes'].items():self.assertIn(identifier,ids);self.assertEqual(len({r['what'],r['why'],r['how']}),3);self.assertTrue(r['unknown_contracts'])

if __name__=='__main__':unittest.main()

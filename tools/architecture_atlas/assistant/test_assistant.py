"""Meaningful local integration checks; no DeepSeek calls or API key required."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from core import Snapshot, Store, SCHEMAS, validate
from server import Agent, Handler, ROOT, ThreadingHTTPServer

TEST_ROOT = ROOT / '.local' / 'checks'
TEST_ROOT.mkdir(parents=True, exist_ok=True)
assert TEST_ROOT.resolve().is_relative_to(ROOT.resolve())


def call(name, args, cid='call1'):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


class SnapshotChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = ROOT.parents[1] / '.atlas-sources/latest.json'
        source = Path(json.loads(manifest.read_text())['root']) if manifest.exists() else None
        cls.snapshot = Snapshot(ROOT, source)

    def test_source_escape_and_lines(self):
        with self.assertRaises(ValueError): self.snapshot.read_source('../../.local/provider.json')
        ref = next(iter(self.snapshot.reviews.values()))['sources'][0]
        result = self.snapshot.read_source(ref['path'], ref['line'], 5)
        self.assertEqual(result['sha'], self.snapshot.sha)
        self.assertIn(str(ref['line'])+':', result['text'])

    def test_paths_are_real_directed_edges_and_variants(self):
        result = self.snapshot.paths('component:orchestrator', 'component:worker', ['depends_on'], 3)
        self.assertTrue(result['paths'])
        for path in result['paths']:
            self.assertEqual(len(path['edge_ids'])+1, len(path['node_ids']))
            for i, eid in enumerate(path['edge_ids']):
                edge = self.snapshot.edges[eid]
                self.assertEqual((edge['source'], edge['target']), tuple(path['node_ids'][i:i+2]))

    def test_removal_changes_graph_without_claiming_runtime(self):
        result = self.snapshot.analyze(['depends_on'], ['component:worker'])
        self.assertEqual(result['baseline']['nodes']-1, result['without_nodes']['nodes'])
        self.assertLess(result['without_nodes']['edges'], result['baseline']['edges'])
        self.assertIn('не доказательство', result['meaning'])

    def test_schema_rejects_executable_unknown_fields_and_nonfinite(self):
        for args in [{'factor': '2'}, {'factor': float('nan')}, {'factor': .8, 'script': 'alert(1)'}]:
            with self.assertRaises(ValueError): validate(args, SCHEMAS['zoom'])

    def test_source_change_after_loading_is_detected(self):
        import hashlib
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            path=Path(folder)/'example.py';path.write_text('before',encoding='utf-8')
            snap=Snapshot(ROOT);snap.files['example.py']=path;snap.expected_hashes['example.py']=hashlib.sha256(path.read_bytes()).hexdigest()
            path.write_text('after',encoding='utf-8')
            with self.assertRaises(ValueError):snap.read_source('example.py')

    def test_memory_survives_reopen_with_sha(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            store = Store(Path(folder)); sid = store.session('oldsha')
            store.append(sid, 'user', 'требование', 'oldsha');store.remember(sid, 'решение', 'oldsha')
            reopened = Store(Path(folder))
            self.assertEqual(reopened.history(sid)[0]['sha'], 'oldsha')
            self.assertEqual(reopened.notes(sid)[0]['text'], 'решение')


class AgentChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.snapshot = Snapshot(ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def wait(self, predicate):
        end = time.monotonic()+4
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.01)
        self.fail('Timed out waiting for local protocol')

    def test_model_cannot_claim_command_before_receipt(self):
        responses = iter([call('select_node', {'node_id': 'component:worker'}), {'role': 'assistant', 'content': 'Подтверждено картой'}])
        agent = Agent(self.snapshot, Path(self.temp.name), provider=lambda _: next(responses))
        sid = agent.store.session(self.snapshot.sha);client = 'client-for-local-test-12345'
        rid = agent.start({'session': sid, 'client': client, 'sha': self.snapshot.sha, 'message': 'выбери Worker'})['id'];run = agent.run(rid, client)
        self.wait(lambda: run['pending'] is not None)
        self.assertEqual(run['status'], 'running');self.assertIsNone(run['result'])
        with self.assertRaises(ValueError): agent.run(rid, 'different-client')
        with run['condition']:
            run['receipt'] = {'ok': True, 'sha': self.snapshot.sha, 'result': {'selected': 'component:worker'}};run['condition'].notify_all()
        self.wait(lambda: run['status'] != 'running')
        run['thread'].join(3)
        self.assertEqual(run['status'], 'completed');self.assertTrue(run['trace'][0]['result']['ok'])

    def test_cancel_awaiting_command_is_not_success(self):
        agent = Agent(self.snapshot, Path(self.temp.name), provider=lambda _: call('view_state', {}))
        sid = agent.store.session(self.snapshot.sha);client = 'cancel-client-local-test-1234'
        rid = agent.start({'session': sid, 'client': client, 'sha': self.snapshot.sha, 'message': 'состояние'})['id'];run = agent.run(rid, client)
        self.wait(lambda: run['pending'] is not None)
        with run['condition']: run['cancelled'] = True;run['condition'].notify_all()
        self.wait(lambda: run['status'] != 'running')
        run['thread'].join(3)
        self.assertEqual(run['status'], 'cancelled');self.assertIsNone(run['result'])

    def test_wrong_snapshot_is_rejected_before_provider(self):
        agent=Agent(self.snapshot,Path(self.temp.name),provider=lambda _: self.fail('Provider must not be called'))
        sid=agent.store.session(self.snapshot.sha)
        with self.assertRaises(ValueError):agent.start({'session':sid,'client':'snapshot-client-check-1234','sha':'wrong','message':'test'})

    def test_failed_receipt_is_not_a_completed_action(self):
        responses=iter([call('view_state',{}),{'role':'assistant','content':'Не подтверждено'}])
        agent=Agent(self.snapshot,Path(self.temp.name),provider=lambda _:next(responses))
        sid=agent.store.session(self.snapshot.sha);client='failed-receipt-check-12345'
        rid=agent.start({'session':sid,'client':client,'sha':self.snapshot.sha,'message':'test'})['id'];run=agent.run(rid,client)
        self.wait(lambda:run['pending'] is not None)
        with run['condition']:run['receipt']={'ok':False,'sha':self.snapshot.sha,'error':'not confirmed'};run['condition'].notify_all()
        self.wait(lambda:run['status']!='running');run['thread'].join(3)
        self.assertEqual(run['status'],'completed_with_tool_errors')

    def test_http_protects_key_backend_and_origin(self):
        agent = Agent(self.snapshot, Path(self.temp.name), provider=lambda _: {'role': 'assistant', 'content': 'test'})
        agent.configure({'api_key': 'local-test-secret', 'model': 'test-model'})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler);server.agent = agent
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base+'/api/assistant/status') as response:
                data = response.read().decode();self.assertNotIn('local-test-secret', data)
            for path in ['/.local/provider.json', '/assistant/server.py', '/scripts/serve.mjs', '/.local/assistant.sqlite3', '/%2e%2e/README.md']:
                with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(base+path)
            request = urllib.request.Request(base+'/api/assistant/session', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'}, method='POST')
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(request)
            with urllib.request.urlopen(base+'/node_modules/three/build/three.module.js') as response: self.assertEqual(response.status, 200)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()

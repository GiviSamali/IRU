import asyncio
import hashlib
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server import file_transfer as relay
from server import database as db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from core import file_transfer as agent
from core import actions


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'db.sqlite3')
    monkeypatch.setenv('IRU_TRANSFER_DIR', str(tmp_path / 'relay'))
    monkeypatch.setattr(relay, 'devices', {
        '1:a': {'user_id': 1, 'ws': object(), 'info': {'home_path': str(Path.home())}},
        '1:b': {'user_id': 1, 'ws': object(), 'info': {'home_path': str(Path.home())}},
        '2:a': {'user_id': 2, 'ws': object(), 'info': {'home_path': str(Path.home())}},
        '2:other': {'user_id': 2, 'ws': object(), 'info': {'home_path': str(Path.home())}},
    })
    relay.init_transfers()
    app = FastAPI()
    app.include_router(relay.router)
    with TestClient(app) as client:
        yield tmp_path, app, client


def row(data=b'abc', owner=1, tid='a'*32, size=None):
    with db.get_db() as conn:
        conn.execute('INSERT INTO file_transfers (transfer_id,owner_user_id,source_device_id,target_device_id,created_at,expires_at,status,upload_hash,download_hash,operation_key,size,sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (tid, owner, 'a', 'b', time.time(), time.time()+3600, 'created', relay.token_hash('up'), relay.token_hash('down'), tid, len(data) if size is None else size, hashlib.sha256(data).hexdigest()))
    return tid


def test_authenticated_one_purpose_stream_and_cleanup(env):
    _, _, client = env
    tid = row()
    url = '/api/transfers/' + tid + '/content'
    assert client.get(url).status_code == 403
    assert client.put(url, content=b'abc', headers={'Authorization': 'Bearer down'}).status_code == 403
    assert client.put(url, content=b'abc', headers={'Authorization': 'Bearer up'}).status_code == 200
    assert client.put(url, content=b'abc', headers={'Authorization': 'Bearer up'}).status_code == 403
    assert client.get(url, headers={'Authorization': 'Bearer up'}).status_code == 403
    response = client.get(url, headers={'Authorization': 'Bearer down'})
    assert response.content == b'abc'
    assert relay.get_transfer(tid)['status'] == 'downloading'  # upload/download alone != success
    assert client.get(url, headers={'Authorization': 'Bearer down'}).status_code == 403
    relay.update(tid, status='completed')
    relay.cleanup_transfers()
    assert not relay.artifact(tid).exists()


@pytest.mark.parametrize('bad_source,bad_target', [('other','b'), ('a','other'), ('a','unknown'), ('2:a','b')])
def test_foreign_or_unknown_device_never_falls_back(env, bad_source, bad_target):
    calls = []
    async def send(*a, **kw): calls.append(a)
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id': bad_source, 'target_device_id': bad_target, 'source_path': 'file'}, send))
    assert result['status'] == 'failed' and calls == []


def test_offline_target_rejected_before_upload(env):
    relay.devices.pop('1:b')
    async def send(*a, **kw): pytest.fail('must not dispatch')
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': 'file'}, send))
    assert result['stage'] == 'validation' and result['status'] == 'failed'


def test_transfer_id_not_enough_and_other_token_rejected(env):
    _, _, client = env
    tid = row()
    assert client.get('/api/transfers/'+tid+'/content', headers={'Authorization': 'Bearer user-b-token'}).status_code == 403
    assert client.put('/api/transfers/'+'b'*32+'/content', content=b'abc', headers={'Authorization': 'Bearer up'}).status_code == 403


def test_expired_and_restart_cleanup(env):
    _, _, client = env
    tid = row()
    relay.artifact(tid).write_bytes(b'abc')
    with db.get_db() as conn:
        conn.execute('UPDATE file_transfers SET expires_at=?', (time.time()-1,))
    assert client.get('/api/transfers/'+tid+'/content', headers={'Authorization': 'Bearer down'}).status_code == 410
    relay.cleanup_transfers()
    assert relay.get_transfer(tid)['status'] == 'expired'
    assert not relay.artifact(tid).exists()
    tid2 = row(tid='b'*32)
    relay.artifact(tid2).write_bytes(b'x')
    relay.cleanup_transfers(restart=True)
    assert relay.get_transfer(tid2)['status'] == 'failed'
    assert not relay.artifact(tid2).exists()


def test_actual_stream_limit_ignores_content_length(env, monkeypatch):
    monkeypatch.setattr(relay, 'MAX_BYTES', 8)
    tid = row(b'12345678')
    class Request:
        headers = {'authorization': 'Bearer up', 'content-length': '8'}
        async def stream(self):
            yield b'12345678'
            yield b'9'
            pytest.fail('must stop consuming immediately')
    with pytest.raises(HTTPException) as error:
        asyncio.run(relay.upload(tid, Request()))
    assert error.value.status_code == 413
    assert not relay.artifact(tid).exists()


def test_500_mib_boundary_and_bounded_stream(env, monkeypatch):
    assert relay.MAX_BYTES == agent.MAX_BYTES == 500 * 1024 * 1024
    agent.check_metadata(agent.MAX_BYTES, 'a'*64)
    with pytest.raises(ValueError): agent.check_metadata(agent.MAX_BYTES+1, 'a'*64)
    # Same production limiter at 8 bytes, exact boundary is accepted, no body() available.
    monkeypatch.setattr(relay, 'MAX_BYTES', 8)
    tid = row(b'12345678')
    class Request:
        headers = {'authorization': 'Bearer up'}
        async def stream(self):
            for part in [b'12', b'34', b'56', b'78']: yield part
    assert asyncio.run(relay.upload(tid, Request()))['size'] == 8


def test_source_missing_directory_and_existing_target(env):
    tmp, _, _ = env
    with pytest.raises(ValueError, match='source_not_found'): agent.stat_source(str(tmp/'missing'))
    with pytest.raises(ValueError, match='directory_transfer_not_supported'): agent.stat_source(str(tmp))
    existing = tmp/'same'; existing.write_bytes(b'keep')
    with pytest.raises(ValueError, match='target_exists'): agent.target_file('same', target_directory=str(tmp))
    assert existing.read_bytes() == b'keep'


@pytest.mark.parametrize('interrupt,bad_hash', [(False, True), (True, False)])
def test_failed_download_never_publishes_or_leaves_part(env, monkeypatch, interrupt, bad_hash):
    tmp, _, _ = env
    class Response:
        status = 200
        calls = 0
        def getheader(self, name): return '3'
        def read(self, size):
            assert size <= agent.CHUNK
            self.calls += 1
            if self.calls == 1: return b'abc'
            if interrupt: raise OSError('disconnect')
            return b''
    class Connection:
        def request(self, *a, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(agent, 'connection', lambda c: Connection())
    with pytest.raises((ValueError, OSError)):
        agent.download({}, str(tmp/'final'), 'a'*32, 'token', 3, 'b'*64 if bad_hash else hashlib.sha256(b'abc').hexdigest())
    assert not (tmp/'final').exists()
    assert not list(tmp.glob('*.iru-part'))


def test_atomic_publish_never_overwrites(env):
    tmp, _, _ = env
    part, final = tmp/'part', tmp/'final'
    part.write_bytes(b'new'); final.write_bytes(b'old')
    with pytest.raises(ValueError, match='target_exists'): agent.publish(part, final)
    assert final.read_bytes() == b'old'


@pytest.fixture
def live(env):
    import uvicorn
    tmp, app, _ = env
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off', ws='none'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(300):
        if server.started: break
        time.sleep(.01)
    assert server.started
    try: yield tmp, {'server_url': f'http://127.0.0.1:{port}'}
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


@pytest.mark.parametrize('data', [b'hello', bytes(range(256))*8193], ids=['text','binary'])
def test_real_http_agent_to_relay_to_agent_and_idempotency(live, monkeypatch, data):
    tmp, config = live
    source, target = tmp/'source.bin', tmp/'target.bin'
    source.write_bytes(data)
    calls = []
    async def send(device, action, params, user_id):
        assert user_id == 1 and device in {'1:a', '1:b'}
        calls.append((device, action))
        return await asyncio.to_thread(agent.dispatch, action, params, config)
    args = {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': str(source), 'target_path': str(target)}
    result = asyncio.run(relay.transfer_file(1, 'same-run', args, send))
    assert result['status'] == 'success', result
    assert target.read_bytes() == source.read_bytes()
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert result['sha256_verified'] is True
    assert not list(relay.storage_root().iterdir())
    again = asyncio.run(relay.transfer_file(1, 'same-run', args, send))
    assert again == result and len(calls) == 4
    assert 'token' not in json.dumps(result) and 'transfer_id' not in result
    assert [a for _, a in calls] == ['file.transfer_stat', 'file.transfer_target', 'file.transfer_upload', 'file.transfer_download']


def test_same_device_id_different_user_dispatch_is_scoped(env):
    called = []
    async def send(device, action, params, user_id):
        called.append((device, user_id))
        return {'error': 'source_not_found'}
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': 'file'}, send))
    assert result['status'] == 'failed' and called == [('1:a', 1)]


def test_different_profiles_fail_closed_and_target_desktop_is_local(env, monkeypatch):
    tmp, _, _ = env
    relay.devices['1:a']['info'] = {'username': 'russa', 'home_path': 'C:/Users/russa'}
    relay.devices['1:b']['info'] = {'username': 'Admin', 'home_path': 'C:/Users/Admin'}
    async def send(*a, **kw): pytest.fail('foreign target path must not dispatch')
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': 'C:/Users/russa/file', 'target_path': 'C:/Users/russa/file'}, send))
    assert result['status'] == 'failed'
    monkeypatch.setattr(actions, 'get_desktop_path', lambda: str(tmp))
    assert agent.target_file('report.docx', target_directory='desktop')['path'] == str(tmp/'report.docx')


def test_upload_hash_mismatch_and_no_success(env):
    _, _, client = env
    tid = row(b'abc')
    response = client.put('/api/transfers/'+tid+'/content', content=b'xyz', headers={'Authorization': 'Bearer up'})
    assert response.status_code == 422 and relay.get_transfer(tid)['status'] == 'failed'
    assert not relay.artifact(tid).exists()


def test_cancel_before_io_and_no_secret_logs(env, caplog):
    async def send(*a, **kw): pytest.fail('cancelled')
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': 'file'}, send, lambda: True))
    assert result['error'] == 'task_cancelled'
    from core.runtime import AgentRuntime
    # Sensitive params must not appear in the logging formatter.
    assert 'SECRET' not in AgentRuntime._format_params_for_log(None, 'file.transfer_upload', {'token': 'SECRET'})


def test_full_500_mib_stream_uses_bounded_python_memory(env, monkeypatch):
    import tracemalloc
    block = b'x' * relay.CHUNK
    digest = hashlib.sha256()
    for _ in range(500): digest.update(block)
    tid = row(size=relay.MAX_BYTES)
    relay.update(tid, sha256=digest.hexdigest())
    class Sink:
        total = 0
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def write(self, data):
            assert len(data) <= relay.CHUNK
            self.total += len(data)
        def flush(self): pass
        def fileno(self): return 123
    sink = Sink()
    class Artifact:
        def open(self, mode): return sink
        def unlink(self, **kw): pass
    monkeypatch.setattr(relay, 'artifact', lambda _: Artifact())
    monkeypatch.setattr(relay.os, 'fsync', lambda _: None)
    class Request:
        headers = {'authorization': 'Bearer up'}
        async def stream(self):
            for _ in range(500): yield block
    tracemalloc.start()
    try:
        result = asyncio.run(relay.upload(tid, Request()))
        peak = tracemalloc.get_traced_memory()[1]
    finally: tracemalloc.stop()
    assert result['size'] == sink.total == 500 * 1024 * 1024
    assert peak < 16 * 1024 * 1024


def test_offline_after_upload_and_publication_denied_after_cancel(live):
    tmp, config = live
    source, target = tmp/'source', tmp/'target'
    source.write_bytes(b'abc')
    calls = []
    async def send(device, action, params, user_id):
        calls.append(action)
        result = await asyncio.to_thread(agent.dispatch, action, params, config)
        if action == 'file.transfer_upload': relay.devices.pop('1:b')
        return result
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id':'a','target_device_id':'b','source_path':str(source),'target_path':str(target)}, send))
    assert result['status'] == 'failed' and result['stage'] == 'download'
    assert 'file.transfer_download' not in calls and not target.exists()
    assert not list(relay.storage_root().iterdir())


def test_cancel_during_pending_action_revokes_credentials(env):
    cancelled = False
    async def send(*a, **kw):
        nonlocal cancelled
        cancelled = True
        await asyncio.sleep(30)
        pytest.fail('pending action should be cancelled')
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id':'a','target_device_id':'b','source_path':'file'}, send, lambda: cancelled))
    assert result['error'] == 'task_cancelled'
    with db.get_db() as conn: entry = dict(conn.execute('SELECT * FROM file_transfers').fetchone())
    assert entry['status'] == 'failed'
    assert entry['upload_hash'] is entry['download_hash'] is entry['finish_hash'] is None


def test_publication_token_is_single_use_and_state_scoped(env):
    _, _, client = env
    tid = row()
    relay.update(tid, finish_hash=relay.token_hash('finish'))
    url = '/api/transfers/'+tid+'/publish'
    assert client.post(url, headers={'Authorization':'Bearer finish'}).status_code == 409
    relay.update(tid, status='downloading')
    assert client.post(url, headers={'Authorization':'Bearer down'}).status_code == 403
    assert client.post(url, headers={'Authorization':'Bearer finish'}).status_code == 200
    assert client.post(url, headers={'Authorization':'Bearer finish'}).status_code == 403


def test_target_rejects_publication_when_relay_revokes_permission(env, monkeypatch):
    tmp, _, _ = env
    class Response:
        status = 200
        sent = False
        def getheader(self, name): return '3'
        def read(self, size):
            assert size <= agent.CHUNK
            if not self.sent:
                self.sent = True
                return b'abc'
            return b''
    class Connection:
        def request(self, method, *a, **kw): self.method = method
        def getresponse(self):
            response = Response()
            if self.method == 'POST': response.status = 409
            return response
        def close(self): pass
    monkeypatch.setattr(agent, 'connection', lambda _: Connection())
    with pytest.raises(ValueError, match='publication_not_authorized'):
        agent.download({}, str(tmp/'final'), 'a'*32, 'down', 3, hashlib.sha256(b'abc').hexdigest(), finish_token='finish')
    assert not (tmp/'final').exists() and not list(tmp.glob('*.iru-part'))


def test_failed_transfer_retry_does_not_reupload(env):
    calls = []
    async def send(*a, **kw): calls.append(a); return {'error':'source_not_found'}
    args = {'source_device_id':'a','target_device_id':'b','source_path':'missing'}
    first = asyncio.run(relay.transfer_file(1, 'same', args, send))
    assert asyncio.run(relay.transfer_file(1, 'same', args, send)) == first
    assert len(calls) == 1


def test_token_for_one_owners_transfer_cannot_access_another(env):
    _, _, client = env
    row()
    foreign = row(owner=2, tid='b'*32)
    relay.update(foreign, upload_hash=relay.token_hash('other-up'), download_hash=relay.token_hash('other-down'))
    assert client.put('/api/transfers/'+foreign+'/content', content=b'abc', headers={'Authorization':'Bearer up'}).status_code == 403
    assert client.get('/api/transfers/'+foreign+'/content', headers={'Authorization':'Bearer down'}).status_code == 403


def test_oversize_source_rejected_before_upload(env):
    calls = []
    async def send(device, action, params, user_id):
        calls.append(action)
        return {'filename':'large','size':relay.MAX_BYTES+1,'sha256':'a'*64}
    result = asyncio.run(relay.transfer_file(1, 'run', {'source_device_id':'a','target_device_id':'b','source_path':'large'}, send))
    assert result['status'] == 'failed' and calls == ['file.transfer_stat']


def test_interrupted_upload_cleans_bytes(env):
    tid = row()
    class Request:
        headers = {'authorization':'Bearer up'}
        async def stream(self):
            yield b'a'
            raise OSError('disconnect')
    with pytest.raises(OSError): asyncio.run(relay.upload(tid, Request()))
    assert not relay.artifact(tid).exists() and relay.get_transfer(tid)['status'] == 'failed'


def test_transfer_rejects_unc_before_path_resolution_and_plain_remote_http():
    with pytest.raises(ValueError, match='network_path_not_supported'):
        agent.checked_path('//foreign-server/share/file')
    with pytest.raises(ValueError, match='transfer_requires_https'):
        agent.connection({'server_url':'ws://example.com'})


def test_cleanup_preserves_completed_idempotency_receipt(env):
    tid = row()
    result = {'status':'success','sha256_verified':True}
    relay.update(tid, status='completed', result_json=json.dumps(result))
    with db.get_db() as conn: conn.execute('UPDATE file_transfers SET expires_at=?', (time.time()-1,))
    relay.cleanup_transfers()
    assert relay.get_transfer(tid)['status'] == 'completed'
    assert json.loads(relay.get_transfer(tid)['result_json']) == result

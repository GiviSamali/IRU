"""Bounded, owner-scoped relay. File bytes never enter controller results."""
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
try:
    from . import database as db
    from .runtime_state import devices, _dk
    from .path_scope import validate_write_path_for_device
except ImportError:
    import database as db
    from runtime_state import devices, _dk
    from path_scope import validate_write_path_for_device

MAX_BYTES = 500 * 1024 * 1024
CHUNK = 1024 * 1024
TTL = 3600
ACTIVE = ('created', 'uploading', 'uploaded', 'downloading', 'committing')
logger = logging.getLogger(__name__)
router = APIRouter()


def storage_root():
    root = Path(os.environ.get('IRU_TRANSFER_DIR', str(db.DB_PATH.parent / 'transfers'))).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def artifact(tid):
    if not re.fullmatch(r'[a-f0-9]{32}', tid):
        raise ValueError('invalid_transfer_id')
    path = storage_root() / tid
    if path.is_symlink() or path.resolve().parent != storage_root():
        raise ValueError('unsafe_transfer_path')
    return path


def init_transfers():
    with db.get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS file_transfers (
          transfer_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,
          source_device_id TEXT NOT NULL, target_device_id TEXT NOT NULL,
          original_filename TEXT NOT NULL DEFAULT '', size INTEGER, sha256 TEXT,
          created_at REAL NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL,
          upload_hash TEXT, download_hash TEXT, finish_hash TEXT, operation_key TEXT NOT NULL,
          result_json TEXT, UNIQUE(owner_user_id, operation_key))""")
        conn.execute('CREATE INDEX IF NOT EXISTS transfer_expiry ON file_transfers(expires_at)')


def get_transfer(tid):
    with db.get_db() as conn:
        row = conn.execute('SELECT * FROM file_transfers WHERE transfer_id=?', (tid,)).fetchone()
        return dict(row) if row else None


def update(tid, **fields):
    allowed = {'status', 'original_filename', 'size', 'sha256', 'result_json', 'upload_hash', 'download_hash', 'finish_hash'}
    if not fields or not set(fields) <= allowed:
        raise ValueError('invalid_transfer_update')
    with db.get_db() as conn:
        conn.execute('UPDATE file_transfers SET ' + ','.join(k+'=?' for k in fields) + ' WHERE transfer_id=?', (*fields.values(), tid))


def remove_artifact(tid):
    artifact(tid).unlink(missing_ok=True)


def cleanup_transfers(restart=False):
    now = time.time()
    with db.get_db() as conn:
        rows = conn.execute('SELECT transfer_id,status,expires_at FROM file_transfers').fetchall()
        for row in rows:
            expired = row['expires_at'] <= now
            interrupted = restart and row['status'] in ACTIVE
            if (expired and row["status"] in ACTIVE) or interrupted:
                result = {'status': 'failed', 'error': 'transfer_expired' if expired else 'server_restarted', 'stage': 'relay'}
                conn.execute('UPDATE file_transfers SET status=?,upload_hash=NULL,download_hash=NULL,finish_hash=NULL,result_json=? WHERE transfer_id=?',
                             ('expired' if expired else 'failed', json.dumps(result), row['transfer_id']))
            if expired or interrupted or row['status'] not in ACTIVE:
                try:
                    remove_artifact(row['transfer_id'])
                except (OSError, ValueError):
                    logger.warning('transfer cleanup deferred id=%s', row['transfer_id'])
        # Keep idempotency tombstones for seven days, never retain file bytes for this period.
        conn.execute('DELETE FROM file_transfers WHERE expires_at < ?', (now - 7 * 86400,))


async def cleanup_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(cleanup_transfers)
        except Exception:
            logger.exception('transfer cleanup failed; retrying next minute')


def require_device(owner, short):
    if not isinstance(short, str) or not short or ':' in short:
        raise ValueError('target_device_not_found')
    dev = devices.get(_dk(owner, short))
    if not dev or dev.get('user_id') != owner or not dev.get('ws'):
        raise ValueError('target_device_not_found_or_offline')
    return dev


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authorize(tid, request, purpose, expected, next_status):
    header = request.headers.get('authorization', '')
    token = header[7:] if header.startswith('Bearer ') else ''
    if not token or len(token) > 256:
        raise HTTPException(403, 'invalid_transfer_credential')
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM file_transfers WHERE transfer_id=?', (tid,)).fetchone()
        if not row or not row[purpose+'_hash'] or not secrets.compare_digest(row[purpose+'_hash'], token_hash(token)):
            raise HTTPException(403, 'invalid_transfer_credential')
        if row['expires_at'] <= time.time():
            raise HTTPException(410, 'transfer_expired')
        if row['status'] != expected:
            raise HTTPException(409, 'transfer_state_conflict')
        try:
            require_device(row['owner_user_id'], row['source_device_id'])
            require_device(row['owner_user_id'], row['target_device_id'])
        except ValueError:
            raise HTTPException(409, 'device_offline')
        conn.execute('UPDATE file_transfers SET status=?, '+purpose+'_hash=NULL WHERE transfer_id=?', (next_status, tid))
        return dict(row)


@router.put('/api/transfers/{tid}/content')
async def upload(tid: str, request: Request):
    row = authorize(tid, request, 'upload', 'created', 'uploading')
    path = artifact(tid)
    count, digest = 0, hashlib.sha256()
    try:
        length = request.headers.get('content-length')
        if length is not None and (int(length) > MAX_BYTES or int(length) != row['size']):
            raise HTTPException(413, 'size_limit_or_mismatch')
        with path.open('xb') as output:
            stream = request.stream().__aiter__()
            while True:
                try:
                    data = await asyncio.wait_for(anext(stream), timeout=min(30, max(.01, row['expires_at'] - time.time())))
                except StopAsyncIteration:
                    break
                if get_transfer(tid)['status'] != 'uploading':
                    raise HTTPException(409, 'transfer_cancelled')
                if count + len(data) > MAX_BYTES or count + len(data) > row['size']:
                    raise HTTPException(413, 'size_limit_or_mismatch')
                # ASGI frames are supplied by the server; disk writes are bounded and off-loop.
                for offset in range(0, len(data), CHUNK):
                    part = data[offset:offset + CHUNK]
                    count += len(part)
                    await asyncio.to_thread(output.write, part)
                    digest.update(part)
                if time.time() >= row['expires_at']:
                    raise HTTPException(410, 'transfer_expired')
            await asyncio.to_thread(output.flush)
            await asyncio.to_thread(os.fsync, output.fileno())
        if count != row['size'] or digest.hexdigest() != row['sha256']:
            raise HTTPException(422, 'integrity_mismatch')
        with db.get_db() as conn:
            changed = conn.execute("UPDATE file_transfers SET status='uploaded' WHERE transfer_id=? AND status='uploading' AND expires_at>?", (tid, time.time())).rowcount
        if not changed:
            raise HTTPException(409, 'transfer_cancelled')
        return {'size': count, 'sha256': digest.hexdigest()}
    except BaseException:
        update(tid, status='failed', upload_hash=None, download_hash=None, finish_hash=None)
        remove_artifact(tid)
        raise


@router.post('/api/transfers/{tid}/publish')
async def permit_publish(tid: str, request: Request):
    authorize(tid, request, 'finish', 'downloading', 'committing')
    return {'publish': True}


@router.get('/api/transfers/{tid}/content')
async def download(tid: str, request: Request):
    row = authorize(tid, request, 'download', 'uploaded', 'downloading')
    try:
        handle = artifact(tid).open('rb')
    except OSError:
        raise HTTPException(410, 'artifact_unavailable')
    async def chunks():
        try:
            while True:
                if time.time() >= row['expires_at'] or get_transfer(tid)['status'] != 'downloading':
                    break
                data = await asyncio.to_thread(handle.read, CHUNK)
                if not data:
                    break
                yield data
        finally:
            handle.close()
    return StreamingResponse(chunks(), media_type='application/octet-stream', headers={'Content-Length': str(row['size']), 'Cache-Control': 'no-store'})


async def transfer_file(owner, run_id, args, send, cancelled=lambda: False):
    """One deterministic operation per run+arguments; repeating never creates another copy."""
    init_transfers()
    stage, tid = 'validation', None
    started = time.monotonic()
    try:
        source, target = args.get('source_device_id'), args.get('target_device_id')
        source_dev, target_dev = require_device(owner, source), require_device(owner, target)
        path = args.get('source_path')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('source_path_required')
        if args.get('target_path') and args.get('target_directory'):
            raise ValueError('choose_target_path_or_directory')
        for device, requested in ((source_dev, path), (target_dev, args.get('target_path') or args.get('target_directory'))):
            if requested and requested != 'desktop':
                validate_write_path_for_device(requested, device.get('info', {}), None)
        key = hashlib.sha256(json.dumps([run_id, args], sort_keys=True).encode()).hexdigest()
        tid, up, down, finish = secrets.token_hex(16), secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        with db.get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM file_transfers WHERE owner_user_id=? AND operation_key=?', (owner, key)).fetchone()
            if old:
                return json.loads(old['result_json']) if old['result_json'] else {'status': 'failed', 'error': 'transfer_in_progress', 'stage': 'relay'}
            active = conn.execute("SELECT owner_user_id FROM file_transfers WHERE status IN ('created','uploading','uploaded','downloading','committing') AND expires_at>?", (now,)).fetchall()
            if len(active) >= 8 or sum(r[0] == owner for r in active) >= 2:
                raise ValueError('transfer_capacity_exceeded')
            conn.execute('INSERT INTO file_transfers (transfer_id,owner_user_id,source_device_id,target_device_id,created_at,expires_at,status,upload_hash,download_hash,finish_hash,operation_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (tid, owner, source, target, now, now+TTL, 'created', token_hash(up), token_hash(down), token_hash(finish), key))
        async def call(device, action, params):
            if cancelled():
                raise ValueError('task_cancelled')
            require_device(owner, source)
            require_device(owner, target)
            remaining = now + TTL - time.time()
            if remaining <= 0:
                raise ValueError('transfer_expired')
            pending = asyncio.create_task(send(_dk(owner, device), action, params, user_id=owner))
            try:
                while not pending.done():
                    if cancelled() or time.time() >= now + TTL:
                        raise ValueError('task_cancelled' if cancelled() else 'transfer_expired')
                    await asyncio.wait({pending}, timeout=0.25)
                result = await pending
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            if cancelled():
                raise ValueError('task_cancelled')
            if not isinstance(result, dict):
                raise ValueError('invalid_agent_result')
            if result.get('error') or result.get('status') in {'failed', 'error'}:
                raise ValueError(str(result.get('error') or 'agent_action_failed'))
            return result
        stage = 'source'
        meta = await call(source, 'file.transfer_stat', {'path': path})
        size, sha = meta.get('size'), meta.get('sha256')
        filename = meta.get('filename')
        if type(size) is not int or size < 0 or size > MAX_BYTES or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('invalid_source_metadata_or_size')
        if not isinstance(filename, str) or not filename or '/' in filename or '\\' in filename or filename in {'.', '..'}:
            raise ValueError('invalid_filename')
        update(tid, original_filename=filename, size=size, sha256=sha)
        if shutil.disk_usage(storage_root()).free < size + 64*1024*1024:
            raise ValueError('relay_disk_full')
        stage = 'target_preflight'
        target_meta = await call(target, 'file.transfer_target', {'filename': filename, 'target_path': args.get('target_path'), 'target_directory': args.get('target_directory')})
        target_path = target_meta.get('path')
        if not isinstance(target_path, str) or not target_path:
            raise ValueError('invalid_target_metadata')
        validate_write_path_for_device(target_path, target_dev.get('info', {}), None)
        stage = 'upload'
        await call(source, 'file.transfer_upload', {'path': path, 'transfer_id': tid, 'token': up, 'size': size, 'sha256': sha})
        if get_transfer(tid)['status'] != 'uploaded':
            raise ValueError('upload_not_verified')
        stage = 'download'
        received = await call(target, 'file.transfer_download', {'path': target_path, 'transfer_id': tid, 'token': down, 'finish_token': finish, 'size': size, 'sha256': sha})
        if received.get('size') != size or received.get('sha256') != sha or received.get('verified') is not True or received.get('path') != target_path:
            raise ValueError('target_integrity_mismatch')
        result = {'status': 'success', 'source_device': source, 'target_device': target, 'filename': filename, 'bytes_transferred': size,
                  'sha256_verified': True, 'sha256': sha, 'target_path': target_path, 'path': target_path}
        if get_transfer(tid)['status'] != 'committing' or time.time() >= now + TTL:
            raise ValueError('transfer_not_committed')
        with db.get_db() as conn:
            changed = conn.execute("UPDATE file_transfers SET status='completed',result_json=?,upload_hash=NULL,download_hash=NULL,finish_hash=NULL WHERE transfer_id=? AND status='committing' AND expires_at>?",
                                   (json.dumps(result), tid, time.time())).rowcount
        if not changed:
            raise ValueError('transfer_not_committed')
        return result
    except (Exception, asyncio.CancelledError) as exc:
        # Do not expose transport URLs/credentials or arbitrary exception text to model/logs.
        reason = str(exc) if isinstance(exc, ValueError) else ('task_cancelled' if isinstance(exc, asyncio.CancelledError) else 'agent_or_transport_failure')
        result = {'status': 'failed', 'stage': stage, 'error': reason[:200]}
        if tid and get_transfer(tid):
            update(tid, status='failed', result_json=json.dumps(result), upload_hash=None, download_hash=None, finish_hash=None)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return result
    finally:
        if tid and get_transfer(tid):
            row = get_transfer(tid)
            try:
                remove_artifact(tid)
            except OSError:
                pass  # Windows open handles: periodic cleanup retries.
            logger.info('transfer id=%s owner=%s source=%s target=%s size=%s status=%s duration=%.2f stage=%s reason=%s',
                        tid, owner, row['source_device_id'], row['target_device_id'], row['size'], row['status'], time.monotonic()-started, stage, (json.loads(row['result_json'] or '{}')).get('error', ''))

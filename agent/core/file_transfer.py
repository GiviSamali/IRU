"""Streaming relay client; only the configured IRU origin receives credentials."""
import hashlib
import http.client
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 500 * 1024 * 1024
CHUNK = 1024 * 1024


def checked_path(raw):
    from .actions import get_desktop_path
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('path_required')
    if raw.startswith(('\\\\', '//')):
        raise ValueError('network_path_not_supported')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # A bare filename refers to this device's Desktop, never another device's home.
        if path.name != raw or raw in {'.', '..'}:
            raise ValueError('absolute_path_required')
        path = Path(get_desktop_path()) / raw
    path = path.resolve()
    norm = str(path).replace('\\', '/').lower()
    home = str(Path.home().resolve()).replace('\\', '/').lower()
    if os.name == 'nt':
        if norm.startswith('//') or ':' in norm[2:]:
            raise ValueError('unsupported_path')
        if re.match(r'^[a-z]:/users/', norm) and not norm.startswith(home + '/'):
            raise ValueError('Путь относится к другому профилю пользователя или устройству')
        if re.match(r'^[a-z]:/(windows|program files(?: \(x86\))?|programdata|system volume information)(/|$)', norm):
            raise ValueError('system_path_forbidden')
    elif any(norm == p or norm.startswith(p + '/') for p in ('/etc', '/bin', '/sbin', '/usr', '/boot', '/dev', '/proc', '/sys', '/var/log', '/root')):
        raise ValueError('system_path_forbidden')
    return path


def source_file(path):
    target = checked_path(path)
    if target.is_dir():
        raise ValueError('directory_transfer_not_supported')
    if target.exists() and not target.is_file():
        raise ValueError('source_not_regular_or_size_limit')
    try:
        handle = target.open('rb')
    except FileNotFoundError:
        raise ValueError('source_not_found')
    metadata = os.fstat(handle.fileno())
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
        handle.close()
        raise ValueError('source_not_regular_or_size_limit')
    return target, handle, metadata.st_size


def stat_source(path):
    target, handle, size = source_file(path)
    digest, count = hashlib.sha256(), 0
    with handle:
        while True:
            data = handle.read(CHUNK)
            if not data:
                break
            count += len(data)
            if count > MAX_BYTES:
                raise ValueError('size_limit')
            digest.update(data)
    if count != size:
        raise ValueError('source_changed')
    return {'filename': target.name, 'size': size, 'sha256': digest.hexdigest()}


def target_file(filename, target_path=None, target_directory=None):
    from .actions import get_desktop_path
    if not filename or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('invalid_filename')
    if target_path and target_directory:
        raise ValueError('choose_target_path_or_directory')
    directory = get_desktop_path() if target_directory in (None, '', 'desktop') else target_directory
    target = checked_path(target_path or str(Path(directory) / filename))
    if target.exists():
        raise ValueError('target_exists')
    if not target.parent.is_dir():
        raise ValueError('target_directory_not_found')
    return {'path': str(target)}


def connection(config):
    origin = urlsplit(config['server_url'])
    if origin.scheme not in {'wss', 'https', 'ws', 'http'} or not origin.hostname or origin.username or origin.password:
        raise ValueError('invalid_server_origin')
    if origin.scheme in {'ws', 'http'} and origin.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('transfer_requires_https')
    cls = http.client.HTTPSConnection if origin.scheme in {'wss', 'https'} else http.client.HTTPConnection
    return cls(origin.hostname, origin.port, timeout=30)


def endpoint(tid):
    if not re.fullmatch('[a-f0-9]{32}', tid):
        raise ValueError('invalid_transfer_id')
    return '/api/transfers/' + tid + '/content'


def check_metadata(size, sha256):
    if type(size) is not int or size < 0 or size > MAX_BYTES or not re.fullmatch('[a-f0-9]{64}', sha256):
        raise ValueError('invalid_transfer_metadata')


def upload(config, path, transfer_id, token, size, sha256):
    check_metadata(size, sha256)
    _, handle, actual = source_file(path)
    conn = connection(config)
    started = time.monotonic()
    try:
        with handle:
            if actual != size:
                raise ValueError('source_changed')
            conn.putrequest('PUT', endpoint(transfer_id))
            conn.putheader('Authorization', 'Bearer ' + token)
            conn.putheader('Content-Length', str(size))
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.endheaders()
            count, digest = 0, hashlib.sha256()
            while count < size:
                if time.monotonic() - started > 1500:
                    raise ValueError('transfer_timeout')
                data = handle.read(min(CHUNK, size-count))
                if not data:
                    raise ValueError('source_changed')
                count += len(data)
                digest.update(data)
                conn.send(data)
            if handle.read(1) or digest.hexdigest() != sha256:
                raise ValueError('source_changed')
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError('upload_rejected_' + str(response.status))
            return {'size': size, 'sha256': sha256}
    finally:
        handle.close()
        conn.close()


def publish(part, target):
    # rename is non-replacing on Windows; hard-link publication is atomic and no-clobber on POSIX.
    try:
        if os.name == 'nt':
            os.rename(part, target)
        else:
            os.link(part, target)
            os.unlink(part)
    except FileExistsError:
        raise ValueError('target_exists')


def download(config, path, transfer_id, token, size, sha256, finish_token=None):
    check_metadata(size, sha256)
    target = checked_path(path)
    if target.exists():
        raise ValueError('target_exists')
    conn = connection(config)
    part = None
    started = time.monotonic()
    try:
        conn.request('GET', endpoint(transfer_id), headers={'Authorization': 'Bearer ' + token})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError('download_rejected_' + str(response.status))
        declared = response.getheader('Content-Length')
        if declared is not None and int(declared) != size:
            raise ValueError('size_mismatch')
        descriptor, name = tempfile.mkstemp(prefix='.iru-', suffix='.iru-part', dir=target.parent)
        part = Path(name)
        count, digest = 0, hashlib.sha256()
        with os.fdopen(descriptor, 'wb') as output:
            while True:
                if time.monotonic() - started > 1500:
                    raise ValueError('transfer_timeout')
                data = response.read(CHUNK)
                if not data:
                    break
                count += len(data)
                if count > size or count > MAX_BYTES:
                    raise ValueError('size_limit_or_mismatch')
                output.write(data)
                digest.update(data)
            output.flush()
            os.fsync(output.fileno())
        if count != size or digest.hexdigest() != sha256:
            raise ValueError('integrity_mismatch')
        # Re-check relay authorization after receiving all bytes: cancel/expiry blocks publication.
        conn.close()
        conn = connection(config)
        conn.request('POST', '/api/transfers/' + transfer_id + '/publish', body=b'',
                     headers={'Authorization': 'Bearer ' + (finish_token or '')})
        if conn.getresponse().status != 200:
            raise ValueError('publication_not_authorized')
        publish(part, target)
        return {'path': str(target), 'size': count, 'sha256': digest.hexdigest(), 'verified': True}
    finally:
        conn.close()
        if part is not None:
            part.unlink(missing_ok=True)


def dispatch(action, params, config):
    try:
        if action == 'file.transfer_stat':
            return stat_source(**params)
        if action == 'file.transfer_target':
            return target_file(**params)
        if action == 'file.transfer_upload':
            return upload(config, **params)
        if action == 'file.transfer_download':
            return download(config, **params)
        raise ValueError('unknown_transfer_action')
    except ValueError as exc:
        return {'error': str(exc), 'status': 'failed'}
    except Exception:
        return {'error': 'transfer_io_failure', 'status': 'failed'}

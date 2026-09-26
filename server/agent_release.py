"""Validated agent releases: immutable archives, atomic manifest publication."""
import hashlib
import json
import os
import re
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")


def validate_version(version):
    if not isinstance(version, str) or len(version) > 32 or not VERSION_RE.fullmatch(version):
        raise ValueError("Version must contain 2-4 numeric components")
    return version


def validate_zip(source, expected):
    validate_version(expected)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        names = [item.orig_filename for item in entries]
        if len(names) != len({name.casefold() for name in names}) or len(names) > 20000:
            raise ValueError("Duplicate ZIP entries or too many files")
        if sum(item.file_size for item in entries) > 1_000_000_000:
            raise ValueError("Unpacked archive is too large")
        for name in names:
            if "\\" in name or ":" in name or name.startswith("/") or any(part in ("", ".", "..") or part.endswith((".", " ")) for part in name.rstrip("/").split("/")):
                raise ValueError("Unsafe archive path")
        roots = [root for root in ("", "IruAgent/") if root + "IruAgent.exe" in names]
        if len(roots) != 1:
            raise ValueError("ZIP must contain one IruAgent.exe at root or in IruAgent/")
        root = roots[0]
        def small_text(name):
            item = archive.getinfo(root + name)
            if item.file_size > 16384:
                raise ValueError("Release metadata is too large")
            return archive.read(item).decode("utf-8-sig").strip()
        actual = small_text("VERSION.txt")
        if actual != expected:
            raise ValueError(f"Archive VERSION.txt is {actual!r}, expected {expected!r}")
        with archive.open(root + "IruAgent.exe") as exe:
            if exe.read(2) != b"MZ":
                raise ValueError("IruAgent.exe is not a Windows executable")
        if root + "BUILD_INFO.json" in names:
            info = json.loads(small_text("BUILD_INFO.json"))
            if info.get("version") != expected or info.get("artifact") != "IruAgent":
                raise ValueError("BUILD_INFO.json does not match the agent release")
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC integrity check failed")
    return actual


@lru_cache(maxsize=8)
def _inspect(path, version, signature):
    validate_zip(path, version)
    with open(path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return digest


def read_release(directory):
    directory = Path(directory)
    manifest = directory / "release.json"
    if not manifest.exists():
        manifest = directory / "version.json"  # legacy metadata, always verified
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    version = validate_version(data.get("version"))
    filename = data.get("filename", "IruAgent.zip")
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.zip", filename):
        raise ValueError("A versioned ZIP release is required")
    path = directory / filename
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    digest = _inspect(str(path), version, signature)
    if data.get("sha256") and data["sha256"] != digest:
        raise ValueError("Release SHA-256 does not match archive")
    if data.get("size") is not None and data["size"] != stat.st_size:
        raise ValueError("Release size does not match archive")
    return dict(data, version=version, kind="zip", filename=filename, sha256=digest, size=stat.st_size)


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

import hashlib
import io
import json
import logging
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import agent_release
from server.routers import agent_update, public


def package(version="3.13.4", root="IruAgent/", missing=False, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(root + "IruAgent.exe", b"MZ" + b"x" * 1500)
        if not missing:
            archive.writestr(root + "VERSION.txt", version)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.fixture
def release_api(tmp_path, monkeypatch):
    directory = tmp_path / "updates"
    monkeypatch.setattr(agent_update, "get_current_user", lambda r: {"id": 1, "name": "admin"})
    monkeypatch.setattr(public, "get_current_user", lambda r: {"id": 1})
    monkeypatch.setattr(agent_update, "add_audit_log", lambda *args: None)
    app = FastAPI()
    app.include_router(agent_update.create_router(directory))
    app.include_router(public.create_router(tmp_path, tmp_path / "exe", directory))
    with TestClient(app) as client:
        yield client, directory


@pytest.mark.parametrize("root", ["", "IruAgent/"])
def test_publish_matches_metadata_zip_and_both_download_routes(release_api, root):
    client, directory = release_api
    body = package(root=root)
    response = client.post("/api/agent/upload?version=3.13.4", content=body)
    assert response.status_code == 200
    info = client.get("/api/agent/version")
    assert info.headers["cache-control"].startswith("no-store")
    data = info.json()
    assert data["sha256"] == hashlib.sha256(body).hexdigest()
    assert not (directory / "version.json").exists()
    assert data["filename"] != "IruAgent.zip"
    for endpoint in [data["download_url"], "/api/download_agent"]:
        download = client.get(endpoint)
        assert download.status_code == 200 and download.content == body
        assert download.headers["x-agent-version"] == "3.13.4"
        assert download.headers["cache-control"].startswith("no-store")
    assert client.get("/api/info").json()["version"] == "3.13.4"


@pytest.mark.parametrize("body", [package("3.8"), package(missing=True), b"MZ" + b"x" * 1500,
    package(extra={"../evil": "x"}), package(extra={"IruAgent/BUILD_INFO.json": '{"version":"3.8","artifact":"IruAgent"}'})])
def test_bad_upload_preserves_published_release(release_api, body):
    client, directory = release_api
    good = package()
    assert client.post("/api/agent/upload?version=3.13.4", content=good).status_code == 200
    before = (directory / "release.json").read_bytes()
    assert client.post("/api/agent/upload?version=3.13.4", content=body).status_code == 400
    assert (directory / "release.json").read_bytes() == before
    assert client.get("/api/agent/download").content == good


def test_no_downgrade_no_replacement_and_stale_url_refused(release_api):
    client, _ = release_api
    good = package()
    client.post("/api/agent/upload?version=3.13.4", content=good)
    old_url = client.get("/api/agent/version").json()["download_url"]
    assert client.post("/api/agent/upload?version=3.13.4", content=good).status_code == 200
    assert client.post("/api/agent/upload?version=3.13.4", content=package(extra={"extra": "x"})).status_code == 409
    assert client.post("/api/agent/upload?version=3.8", content=package("3.8")).status_code == 409
    assert client.post("/api/agent/upload?version=3.13.5", content=package("3.13.5")).status_code == 200
    assert client.get(old_url).status_code == 409


@pytest.mark.parametrize("bad", [False, True])
def test_legacy_metadata_is_verified_not_trusted(release_api, bad):
    client, directory = release_api
    directory.mkdir()
    (directory / "version.json").write_text(json.dumps({"version": "3.13.4", "filename": "IruAgent.zip", "kind": "zip"}))
    (directory / "IruAgent.zip").write_bytes(package("3.8" if bad else "3.13.4"))
    assert client.get("/api/agent/version").status_code == (503 if bad else 200)
    assert client.get("/api/agent/download").status_code == (503 if bad else 200)
    if bad:
        assert client.post("/api/agent/upload?version=3.13.4", content=package()).status_code == 200
        assert client.get("/api/agent/version").status_code == 200


def test_missing_or_corrupt_release_fails_closed(release_api):
    client, directory = release_api
    assert client.get("/api/agent/version").status_code == 503
    client.post("/api/agent/upload?version=3.13.4", content=package())
    manifest = directory / "release.json"
    data = json.loads(manifest.read_text())
    data["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data))
    assert client.get("/api/agent/download").status_code == 503
    manifest.write_text("{")
    assert client.get("/api/agent/version").status_code == 503


def test_manifest_write_failure_preserves_previous_release(release_api, monkeypatch):
    client, directory = release_api
    good = package()
    client.post("/api/agent/upload?version=3.13.4", content=good)
    original = agent_update.atomic_write
    def fail_manifest(path, data):
        if path.name == "release.json":
            raise OSError("disk full")
        original(path, data)
    monkeypatch.setattr(agent_update, "atomic_write", fail_manifest)
    with pytest.raises(OSError):
        client.post("/api/agent/upload?version=3.13.5", content=package("3.13.5"))
    assert client.get("/api/agent/version").json()["version"] == "3.13.4"
    assert client.get("/api/agent/download").content == good


def test_upload_requires_admin(release_api, monkeypatch):
    client, _ = release_api
    monkeypatch.setattr(agent_update, "get_current_user", lambda r: {"id": 2})
    assert client.post("/api/agent/upload?version=3.13.4", content=package()).status_code == 403


@pytest.mark.parametrize("case", ["valid", "wrong_version", "wrong_hash", "missing_version", "unsafe_path"])
def test_client_never_installs_mismatched_zip(tmp_path, monkeypatch, case):
    agent_dir = str(Path(__file__).resolve().parents[1] / "agent")
    monkeypatch.syspath_prepend(agent_dir)
    from core import update
    body = package("3.8" if case == "wrong_version" else "3.13.4", missing=case == "missing_version",
                   extra={"../evil": "x"} if case == "unsafe_path" else None)
    metadata = {"version": "3.13.4", "kind": "zip", "download_url": "/api/agent/download",
                "sha256": "0" * 64 if case == "wrong_hash" else hashlib.sha256(body).hexdigest(), "size": len(body)}
    monkeypatch.setattr(update, "platform_name", lambda: "Windows")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(metadata).encode()))
    downloaded = []
    def download(**kwargs):
        kwargs["download_path"].write_bytes(body); downloaded.append(kwargs["download_path"])
        return len(body)
    monkeypatch.setattr(update, "_download_update", download)
    installs = []
    monkeypatch.setattr(update, "_update_zip", lambda *a: installs.append(a) or True)
    state = SimpleNamespace(set_update_status=lambda *a, **k: None)
    assert update.check_for_update("wss://example.invalid", "3.8", None, logging.getLogger("test"), state) == (case == "valid")
    assert bool(installs) == (case == "valid")
    if case != "valid":
        assert not downloaded[0].exists()
    for path in downloaded:
        path.unlink(missing_ok=True)


def test_replaced_archive_detected_after_cached_read(release_api):
    client, directory = release_api
    client.post("/api/agent/upload?version=3.13.4", content=package())
    data = client.get("/api/agent/version").json()
    (directory / data["filename"]).write_bytes(package("3.8"))
    assert client.get("/api/agent/version").status_code == 503
    assert client.get("/api/download_agent").status_code == 503


@pytest.mark.parametrize("version", ["3.13.4&bad=x", "../../evil", "latest", "3"])
def test_invalid_version_rejected(release_api, version):
    client, _ = release_api
    assert client.post("/api/agent/upload", params={"version": version}, content=package()).status_code == 400

def test_public_info_returns_json(client):
    response = client.get("/api/info")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert isinstance(payload, dict)
    assert payload
    assert payload.get("name")
    assert payload.get("server")
    if "version" in payload:
        assert isinstance(payload["version"], str)
        assert payload["version"].strip()


def test_public_plans_returns_expected_plan_keys(client):
    response = client.get("/api/plans")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert isinstance(payload, dict)

    plans = payload.get("plans", payload)
    assert isinstance(plans, dict)
    assert {"free", "pro", "business"}.issubset(plans.keys())

    for plan_name in ("free", "pro", "business"):
        assert isinstance(plans[plan_name], dict)
        assert plans[plan_name]


def test_root_versions_voice_and_submission_assets_and_revalidates_html(client):
    import hashlib
    import re
    from server.main import UI_DIR

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    for name in ("chat.js", "voice-session.js", "voice.js"):
        revision = hashlib.sha256((UI_DIR / "js" / name).read_bytes()).hexdigest()[:16]
        urls = re.findall(r'src="(js/' + re.escape(name) + r'\?v=[0-9a-f]+)"', response.text)
        assert urls == [f"js/{name}?v={revision}"]
        asset = client.get("/" + urls[0])
        assert asset.status_code == 200
        assert asset.content == (UI_DIR / "js" / name).read_bytes()


def test_voice_asset_url_changes_with_content_even_if_mtime_is_unchanged(tmp_path):
    import os
    import re
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.routers.public import create_router

    (tmp_path / "js").mkdir()
    (tmp_path / "index.html").write_text('<script src="js/voice-session.js"></script>', encoding="utf-8")
    asset = tmp_path / "js" / "voice-session.js"
    asset.write_text("old", encoding="utf-8")
    original_stat = asset.stat()
    app = FastAPI()
    app.include_router(create_router(tmp_path, tmp_path / "downloads"))
    with TestClient(app) as client:
        old = client.get("/")
        asset.write_text("new", encoding="utf-8")
        os.utime(asset, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        new = client.get("/")
        assert old.headers["cache-control"] == new.headers["cache-control"] == "no-cache"
        assert re.search(r'\?v=([0-9a-f]+)', old.text).group(1) != re.search(r'\?v=([0-9a-f]+)', new.text).group(1)

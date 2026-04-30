import hashlib


def _create_test_user():
    from server.database import create_user

    return create_user("auth-test-user")


def test_legacy_token_login_returns_access_and_refresh_tokens(client):
    user = _create_test_user()

    response = client.post("/api/auth", json={"token": user["token"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload.get("access_token"), str) and payload["access_token"]
    assert isinstance(payload.get("refresh_token"), str) and payload["refresh_token"]
    assert payload["user"]["id"] == user["id"]


def test_invalid_legacy_token_login_fails(client):
    response = client.post("/api/auth", json={"token": "invalid-token"})

    assert response.status_code == 401
    payload = response.json()
    assert payload["status"] == "error"


def test_authorized_request_via_bearer_access_token(client):
    user = _create_test_user()
    login_response = client.post("/api/auth", json={"token": user["token"]})
    access_token = login_response.json()["access_token"]

    response = client.get("/api/user_info", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["user"]["id"] == user["id"]


def test_refresh_flow_returns_new_access_token_and_stores_hashed_refresh_token(client):
    from server.database import get_db

    user = _create_test_user()
    login_response = client.post("/api/auth", json={"token": user["token"]})
    login_payload = login_response.json()
    refresh_token = login_payload["refresh_token"]
    original_access_token = login_payload["access_token"]

    with get_db() as conn:
        row = conn.execute(
            "SELECT token FROM refresh_tokens WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

    assert row is not None
    assert row["token"] == hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

    refresh_response = client.post("/api/refresh", json={"refresh_token": refresh_token})

    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["status"] == "ok"
    assert isinstance(refresh_payload.get("access_token"), str)
    assert refresh_payload["access_token"]
    assert refresh_payload["access_token"] != original_access_token


def test_logout_invalidates_refresh_token(client):
    user = _create_test_user()
    login_response = client.post("/api/auth", json={"token": user["token"]})
    login_payload = login_response.json()
    refresh_token = login_payload["refresh_token"]
    access_token = login_payload["access_token"]

    logout_response = client.post(
        "/api/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert logout_response.status_code == 200
    assert logout_response.json()["status"] == "ok"

    refresh_response = client.post("/api/refresh", json={"refresh_token": refresh_token})

    assert refresh_response.status_code == 401
    payload = refresh_response.json()
    assert payload["status"] == "error"


def test_legacy_x_token_still_authorizes_protected_endpoint(client):
    user = _create_test_user()

    response = client.get("/api/user_info", headers={"X-Token": user["token"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["user"]["id"] == user["id"]

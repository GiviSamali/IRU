from server.database import (
    PLAN_LIMITS,
    check_daily_command_limit,
    check_device_limit,
    create_user,
    increment_daily_commands,
    set_user_plan,
)
from server.runtime_state import ip_rate_counters, rate_counters


def _create_user_with_plan(plan: str):
    user = create_user(f"{plan}-plan-user")
    assert set_user_plan(user["id"], plan) is True
    return user


def _login_headers(client, user: dict) -> dict:
    response = client.post("/api/auth", json={"token": user["token"]})
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def _reset_rate_limits():
    ip_rate_counters.clear()
    rate_counters.clear()


def test_free_plan_has_expected_limits():
    user = _create_user_with_plan("free")

    assert PLAN_LIMITS["free"]["max_devices"] == 1
    assert PLAN_LIMITS["free"]["max_commands_per_day"] == 30
    assert PLAN_LIMITS["free"]["dev_mode"] is False

    first_device_check = check_device_limit(user["id"], current_device_count=0)
    second_device_check = check_device_limit(user["id"], current_device_count=1)

    assert first_device_check["allowed"] is True
    assert second_device_check["allowed"] is False
    assert second_device_check["limit"] == 1

    for _ in range(30):
        increment_daily_commands(user["id"])

    daily_limit = check_daily_command_limit(user["id"])
    assert daily_limit["used"] == 30
    assert daily_limit["limit"] == 30
    assert daily_limit["allowed"] is False


def test_pro_plan_has_paid_limits_and_dev_mode_endpoint_access(client):
    user = _create_user_with_plan("pro")
    headers = _login_headers(client, user)
    _reset_rate_limits()

    assert PLAN_LIMITS["pro"]["max_devices"] > 1
    assert PLAN_LIMITS["pro"]["max_commands_per_day"] > PLAN_LIMITS["free"]["max_commands_per_day"]
    assert PLAN_LIMITS["pro"]["dev_mode"] is True

    second_device_check = check_device_limit(user["id"], current_device_count=1)
    assert second_device_check["allowed"] is True

    for _ in range(30):
        increment_daily_commands(user["id"])

    daily_limit = check_daily_command_limit(user["id"])
    assert daily_limit["used"] == 30
    assert daily_limit["allowed"] is True

    response = client.post(
        "/api/raw_command",
        headers=headers,
        json={"command": "whoami", "broadcast": True},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "error"
    assert "Режим разработчика" not in payload["error"]


def test_business_plan_behaves_like_paid_plan_not_free(client):
    user = _create_user_with_plan("business")
    headers = _login_headers(client, user)
    _reset_rate_limits()

    assert PLAN_LIMITS["business"]["max_devices"] > 1
    assert PLAN_LIMITS["business"]["max_commands_per_day"] > PLAN_LIMITS["free"]["max_commands_per_day"]
    assert PLAN_LIMITS["business"]["dev_mode"] is True

    second_device_check = check_device_limit(user["id"], current_device_count=1)
    assert second_device_check["allowed"] is True

    for _ in range(30):
        increment_daily_commands(user["id"])

    daily_limit = check_daily_command_limit(user["id"])
    assert daily_limit["used"] == 30
    assert daily_limit["allowed"] is True

    raw_command_response = client.post(
        "/api/raw_command",
        headers=headers,
        json={"command": "whoami", "broadcast": True},
    )
    raw_command_payload = raw_command_response.json()

    assert raw_command_response.status_code == 200
    assert raw_command_payload["status"] == "error"
    assert "Режим разработчика" not in raw_command_payload["error"]

    chat_response = client.post("/api/chats", headers=headers, json={"title": "plan check"})
    chat_id = chat_response.json()["chat"]["id"]
    _reset_rate_limits()

    run_plan_response = client.post(
        f"/api/run_plan/{chat_id}",
        headers=headers,
        json={"original_request": "Составь план"},
    )
    run_plan_payload = run_plan_response.json()

    assert run_plan_response.status_code == 200
    assert run_plan_payload["status"] == "error"
    assert "подтверждение" not in run_plan_payload.get("error", "").lower()

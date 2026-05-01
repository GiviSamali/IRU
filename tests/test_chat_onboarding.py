import time


def _create_and_login_user(client):
    from server.database import create_user

    user = create_user("onboarding-test-user")
    response = client.post("/api/auth", json={"token": user["token"]})
    payload = response.json()
    return user, {"Authorization": f"Bearer {payload['access_token']}"}


def test_api_chat_uses_onboarding_mode_without_devices(client, monkeypatch):
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime

    user, headers = _create_and_login_user(client)

    runtime_state.devices = {
        device_id: device
        for device_id, device in runtime_state.devices.items()
        if device.get("user_id") != user["id"]
    }

    assert runtime_state.get_user_devices(user["id"]) == {}

    called = {"onboarding": 0, "agent": 0}

    async def fake_process_onboarding_message(user_message: str, chat_history=None):
        called["onboarding"] += 1
        return {"answer": f"Onboarding help: {user_message}"}

    async def fake_send_command_to_agent(*args, **kwargs):
        called["agent"] += 1
        raise AssertionError("send_command_to_agent should not be called in onboarding mode")

    monkeypatch.setattr(task_runtime, "process_onboarding_message", fake_process_onboarding_message)
    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send_command_to_agent)

    response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Как подключить агент?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["task_id"]
    assert payload["chat_id"]
    assert payload["device_ids"] == []

    task_id = payload["task_id"]
    task_payload = None
    for _ in range(20):
        task_response = client.get(f"/api/tasks/{task_id}", headers=headers)
        assert task_response.status_code == 200
        task_payload = task_response.json()["task"]
        if task_payload["status"] == "done":
            break
        time.sleep(0.05)

    assert task_payload is not None
    assert task_payload["status"] == "done"
    assert "Onboarding help:" in (task_payload.get("answer") or "")
    assert task_payload["device_ids"] == []
    assert task_payload.get("commands") == []
    assert called["onboarding"] == 1
    assert called["agent"] == 0

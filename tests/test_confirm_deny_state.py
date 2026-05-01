import json
import time


def _create_and_login_user(client):
    from server.database import create_user

    user = create_user("confirm-deny-test-user")
    response = client.post("/api/auth", json={"token": user["token"]})
    payload = response.json()
    return user, {"Authorization": f"Bearer {payload['access_token']}"}


def _wait_for_task_status(client, headers, task_id, expected_status, attempts=30):
    last_payload = None
    for _ in range(attempts):
        response = client.get(f"/api/tasks/{task_id}", headers=headers)
        assert response.status_code == 200
        last_payload = response.json()["task"]
        if last_payload["status"] == expected_status:
            return last_payload
        time.sleep(0.05)
    assert last_payload is not None
    raise AssertionError(f"Task {task_id} did not reach status {expected_status!r}: {last_payload}")


def test_deny_is_bound_only_to_original_task_and_new_request_requires_confirm_again(client, monkeypatch):
    import server.controller as controller
    import server.routers.tasks as tasks_router
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime

    user, headers = _create_and_login_user(client)
    device_key = f"{user['id']}:device-1"
    monkeypatch.setattr(tasks_router, "devices", runtime_state.devices)
    monkeypatch.setattr(task_runtime, "devices", runtime_state.devices)
    runtime_state.devices[device_key] = {
        "user_id": user["id"],
        "info": {"hostname": "workstation", "os": "Windows", "os_version": "11"},
        "pending": {},
    }

    llm_calls = {"count": 0}

    async def fake_chat_completion_request(**kwargs):
        llm_calls["count"] += 1
        messages = kwargs["messages"]
        assistant_texts = [msg.get("content", "") for msg in messages if msg.get("role") == "assistant"]
        assert "Команда отменена пользователем." not in assistant_texts
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{llm_calls['count']}",
                        "function": {
                            "name": "execute_cmd",
                            "arguments": json.dumps({"command": r'Remove-Item "C:\Temp\demo.txt"'}),
                        },
                    }],
                },
            }]
        }

    async def fake_send_command_to_agent(device_id, action, params, user_id=None, skip_confirm=False):
        assert device_id == device_key
        assert action == "execute_cmd"
        if skip_confirm:
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise RuntimeError("CONFIRM_REQUIRED: Команда требует подтверждения пользователя.")

    monkeypatch.setattr(controller, "load_llm_config", lambda: {
        "model": "mock-model",
        "max_tokens": 256,
        "base_url": "http://unused",
        "api_key": "unused",
    })
    async def fake_classify_task_complexity(message):
        return ("SIMPLE", "")

    monkeypatch.setattr(task_runtime, "classify_task_complexity", fake_classify_task_complexity)
    monkeypatch.setattr(controller, "_chat_completion_request", fake_chat_completion_request)
    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send_command_to_agent)

    first_response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Удали файл C:\\Temp\\demo.txt", "device_id": "device-1"},
    )
    assert first_response.status_code == 200
    first_payload = first_response.json()
    first_task_id = first_payload["task_id"]

    first_task = _wait_for_task_status(client, headers, first_task_id, "confirm")
    assert first_task["confirm_data"]["device_id"] == "device-1"
    assert "подтверждения" in (first_task["answer"] or "").lower()

    deny_response = client.post(f"/api/tasks/{first_task_id}/deny", headers=headers)
    assert deny_response.status_code == 200
    assert deny_response.json()["status"] == "ok"

    denied_task = _wait_for_task_status(client, headers, first_task_id, "done")
    assert denied_task["answer"] == "Команда отменена пользователем."
    assert denied_task["confirm_data"] is None

    second_response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Удали файл C:\\Temp\\demo.txt", "device_id": "device-1"},
    )
    assert second_response.status_code == 200
    second_payload = second_response.json()
    second_task_id = second_payload["task_id"]

    assert second_task_id != first_task_id

    second_task = _wait_for_task_status(client, headers, second_task_id, "confirm")
    assert second_task["status"] == "confirm"
    assert second_task["answer"] != "Команда отменена пользователем."
    assert second_task["confirm_data"]["device_id"] == "device-1"
    assert llm_calls["count"] == 2

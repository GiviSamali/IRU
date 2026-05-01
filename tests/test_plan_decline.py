import time


def _create_and_login_user(client):
    from server.database import create_user

    user = create_user("plan-decline-test-user")
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


def test_decline_plan_blocks_repeat_suggestion_for_same_request(client, monkeypatch):
    import server.routers.tasks as tasks_router
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime

    user, headers = _create_and_login_user(client)
    device_key = f"{user['id']}:device-1"
    request_text = "Составь и выполни план очистки папки загрузок"

    monkeypatch.setattr(tasks_router, "devices", runtime_state.devices)
    monkeypatch.setattr(task_runtime, "devices", runtime_state.devices)
    monkeypatch.setattr(tasks_router, "tasks", runtime_state.tasks)
    monkeypatch.setattr(task_runtime, "tasks", runtime_state.tasks)
    runtime_state.devices[device_key] = {
        "user_id": user["id"],
        "info": {"hostname": "workstation", "os": "Windows", "os_version": "11"},
        "pending": {},
    }

    calls = {"classify": 0, "process": 0}

    async def fake_classify_task_complexity(message):
        calls["classify"] += 1
        assert message == request_text
        return ("PLAN", "Нужен пошаговый план")

    async def fake_process_nl_command(**kwargs):
        calls["process"] += 1
        assert kwargs["user_message"] == request_text
        return {
            "answer": "[[SUGGEST_PLAN: Этот план не должен появиться снова]]",
            "commands": [],
            "tasks": [],
        }

    monkeypatch.setattr(task_runtime, "classify_task_complexity", fake_classify_task_complexity)
    monkeypatch.setattr(task_runtime, "process_nl_command", fake_process_nl_command)

    first_response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": request_text, "device_id": "device-1"},
    )
    assert first_response.status_code == 200
    first_payload = first_response.json()
    first_task_id = first_payload["task_id"]
    chat_id = first_payload["chat_id"]

    first_task = _wait_for_task_status(client, headers, first_task_id, "done")
    assert first_task.get("plan_suggestion") == "Нужен пошаговый план"
    assert first_task.get("plan_original_request") == request_text
    assert first_task.get("answer") in {"", None}

    decline_response = client.post(f"/api/tasks/{first_task_id}/decline_plan", headers=headers)
    assert decline_response.status_code == 200
    assert decline_response.json()["status"] == "ok"

    declined_task_response = client.get(f"/api/tasks/{first_task_id}", headers=headers)
    assert declined_task_response.status_code == 200
    declined_task = declined_task_response.json()["task"]
    assert "plan_suggestion" not in declined_task

    second_response = client.post(
        "/api/chat",
        headers=headers,
        json={"message": request_text, "device_id": "device-1", "chat_id": chat_id},
    )
    assert second_response.status_code == 200
    second_payload = second_response.json()
    second_task_id = second_payload["task_id"]

    assert second_task_id != first_task_id

    second_task = _wait_for_task_status(client, headers, second_task_id, "done")
    assert second_task.get("plan_suggestion") is None
    assert second_task.get("answer")
    assert second_task["answer"].strip().lower() != "готово."
    assert "план отключ" in second_task["answer"].lower()
    assert calls["classify"] == 1
    assert calls["process"] == 1

import time


def _auth_headers(client, name="cancel-test-user"):
    from server.database import create_user

    user = create_user(name)
    payload = client.post("/api/auth", json={"token": user["token"]}).json()
    return user, {"Authorization": f"Bearer {payload['access_token']}"}


def test_cancel_endpoint_marks_owned_running_task_cancelling(client):
    import server.runtime_state as runtime_state

    user, headers = _auth_headers(client)
    task_id = "cancel-running-task"
    runtime_state.tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": 1,
        "message": "long task",
        "device_ids": ["device-1"],
        "status": "running",
        "created_at": time.time(),
        "commands": [],
        "tasks": [],
    }

    response = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["task_status"] == "cancelling"
    assert data["cancel_requested"] is True
    assert runtime_state.tasks[task_id]["status"] == "cancelling"
    assert runtime_state.tasks[task_id]["cancel_requested"] is True
    assert "Текущий инструмент" in data["message"]


def test_cancel_endpoint_does_not_reopen_terminal_task(client):
    import server.runtime_state as runtime_state

    user, headers = _auth_headers(client, "cancel-terminal-user")
    task_id = "cancel-done-task"
    runtime_state.tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": 1,
        "message": "done task",
        "device_ids": ["device-1"],
        "status": "done",
        "created_at": time.time(),
        "commands": [],
        "tasks": [],
    }

    response = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["task_status"] == "done"
    assert runtime_state.tasks[task_id]["status"] == "done"
    assert not runtime_state.tasks[task_id].get("cancel_requested")


def test_cancel_endpoint_finishes_confirm_task_immediately(client):
    import server.runtime_state as runtime_state

    user, headers = _auth_headers(client, "cancel-confirm-user")
    task_id = "cancel-confirm-task"
    runtime_state.tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": 1,
        "message": "confirm task",
        "device_ids": ["device-1"],
        "status": "confirm",
        "created_at": time.time(),
        "commands": [],
        "tasks": [],
        "confirm_data": {"command": "dangerous"},
    }

    response = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["task_status"] == "cancelled"
    assert runtime_state.tasks[task_id]["status"] == "cancelled"
    assert runtime_state.tasks[task_id]["answer"] == "Остановлено пользователем."

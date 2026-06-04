import time


def _create_and_login_user(client, name="memory-lifecycle-user"):
    from server.database import create_user

    user = create_user(name)
    response = client.post("/api/auth", json={"token": user["token"]})
    payload = response.json()
    return user, {"Authorization": f"Bearer {payload['access_token']}"}


def _register_profile(user, device_id="device-1", machine_guid="machine-1"):
    from server.database import upsert_device_profile

    upsert_device_profile(
        device_id,
        user["id"],
        {"hostname": "workstation", "os": "Windows", "machine_guid": machine_guid},
    )


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


def test_user_memory_fact_delete_disappears_from_memory_stats(client):
    from server.database import add_user_fact

    user, headers = _create_and_login_user(client)
    _register_profile(user)
    fact_id = add_user_fact(str(user["id"]), "likes tea", "preference")

    before = client.get("/api/memory/stats?device_id=device-1", headers=headers)
    assert before.status_code == 200
    assert before.json()["memory_stats"]["facts_list"] == [
        {"id": fact_id, "text": "likes tea", "category": "preference", "source": "user"}
    ]

    deleted = client.post(
        "/api/memory/facts/delete",
        headers=headers,
        json={"id": fact_id, "source": "user", "device_id": "device-1"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["memory_stats"]["facts_list"] == []

    after = client.get("/api/memory/stats?device_id=device-1", headers=headers)
    assert after.json()["memory_stats"]["facts_list"] == []


def test_memory_facts_api_add_list_delete_user_fact(client):
    user, headers = _create_and_login_user(client, "memory-panel-owner")

    empty = client.get("/api/memory/facts", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {"status": "ok", "facts": []}

    created = client.post(
        "/api/memory/facts",
        headers=headers,
        json={"text": "works on IRU", "category": "project"},
    )
    assert created.status_code == 200
    fact = created.json()["fact"]
    assert fact["text"] == "works on IRU"
    assert fact["category"] == "project"
    assert fact["source"] == "user"
    assert fact["created_at"]

    listed = client.get("/api/memory/facts", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["facts"] == [fact]

    deleted = client.delete(f"/api/memory/facts/{fact['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["facts"] == []


def test_memory_facts_api_does_not_delete_other_users_fact(client):
    from server.database import add_user_fact, get_user_facts

    user, headers = _create_and_login_user(client, "memory-panel-delete-owner")
    other, _ = _create_and_login_user(client, "memory-panel-delete-other")
    other_fact_id = add_user_fact(str(other["id"]), "other private fact", "private")

    deleted = client.delete(f"/api/memory/facts/{other_fact_id}", headers=headers)
    assert deleted.status_code == 404
    assert [fact["fact_text"] for fact in get_user_facts(str(other["id"]))] == ["other private fact"]


def test_legacy_device_memory_fact_delete_unpins_from_memory_stats(client):
    from server.database import add_fact, get_memory_stats

    user, headers = _create_and_login_user(client)
    _register_profile(user, machine_guid="legacy-machine")
    fact_id = add_fact("legacy-machine", "device-1", "legacy pinned fact", "config")

    before = get_memory_stats("legacy-machine", str(user["id"]))
    assert before["facts_list"] == [
        {"id": fact_id, "text": "legacy pinned fact", "category": "config", "source": "device"}
    ]

    deleted = client.post(
        "/api/memory/facts/delete",
        headers=headers,
        json={"id": fact_id, "source": "device", "device_id": "device-1"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["memory_stats"]["facts_list"] == []
    assert get_memory_stats("legacy-machine", str(user["id"]))["facts_list"] == []


def test_device_memory_fact_delete_requires_explicit_device_id(client):
    from server.database import add_fact

    user, headers = _create_and_login_user(client)
    _register_profile(user, machine_guid="legacy-machine")
    fact_id = add_fact("legacy-machine", "device-1", "legacy pinned fact", "config")

    deleted = client.post(
        "/api/memory/facts/delete",
        headers=headers,
        json={"id": fact_id, "source": "device"},
    )
    assert deleted.status_code == 400
    assert deleted.json()["detail"] == "Device id required for device memory source"


def test_delete_user_fact_does_not_delete_other_users_fact(client):
    from server.database import add_user_fact, get_user_facts

    user, headers = _create_and_login_user(client, "memory-owner")
    other, _ = _create_and_login_user(client, "memory-other")
    _register_profile(user)
    other_fact_id = add_user_fact(str(other["id"]), "private other fact", "private")

    deleted = client.post(
        "/api/memory/facts/delete",
        headers=headers,
        json={"id": other_fact_id, "source": "user", "device_id": "device-1"},
    )
    assert deleted.status_code == 404
    assert [fact["fact_text"] for fact in get_user_facts(str(other["id"]))] == ["private other fact"]


def test_decline_suggested_fact_does_not_insert_user_memory(client, monkeypatch):
    import server.routers.tasks as tasks_router
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime
    from server.database import get_user_facts

    user, headers = _create_and_login_user(client)
    device_key = f"{user['id']}:device-1"
    request_text = "Remember suggestion?"

    monkeypatch.setattr(tasks_router, "devices", runtime_state.devices)
    monkeypatch.setattr(task_runtime, "devices", runtime_state.devices)
    monkeypatch.setattr(tasks_router, "tasks", runtime_state.tasks)
    monkeypatch.setattr(task_runtime, "tasks", runtime_state.tasks)
    runtime_state.devices[device_key] = {
        "user_id": user["id"],
        "info": {"hostname": "workstation", "os": "Windows", "machine_guid": "machine-1"},
        "pending": {},
    }

    async def fake_classify_task_complexity(message):
        return ("SIMPLE", "")

    async def fake_process_nl_command(**kwargs):
        return {
            "answer": "Ok. [[SUGGEST_REMEMBER: Python lives in C:/Python | config]]",
            "commands": [
                {
                    "action": "execute_cmd",
                    "command": r'py -3 -c "import sys; print(sys.executable); print(sys.version)"',
                    "result": {
                        "stdout": r"C:\Program Files\Python311\python.exe" + "\n3.11.9 (main, Apr  2 2024)\n",
                        "stderr": "",
                        "returncode": 0,
                    },
                }
            ],
            "tasks": [],
        }

    monkeypatch.setattr(task_runtime, "classify_task_complexity", fake_classify_task_complexity)
    monkeypatch.setattr(task_runtime, "process_nl_command", fake_process_nl_command)

    created = client.post("/api/chat", headers=headers, json={"message": request_text, "device_id": "device-1"})
    assert created.status_code == 200
    task = _wait_for_task_status(client, headers, created.json()["task_id"], "done")
    assert task["suggested_fact"]["text"] == "Python lives in C:/Python"

    declined = client.post(f"/api/tasks/{created.json()['task_id']}/decline_fact", headers=headers)
    assert declined.status_code == 200
    assert declined.json()["status"] == "ok"
    assert get_user_facts(str(user["id"])) == []


def test_assistant_memory_claim_without_remember_action_is_not_memory_write(client, monkeypatch):
    import server.routers.tasks as tasks_router
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime
    from server.database import get_user_facts

    user, headers = _create_and_login_user(client)
    device_key = f"{user['id']}:device-1"

    monkeypatch.setattr(tasks_router, "devices", runtime_state.devices)
    monkeypatch.setattr(task_runtime, "devices", runtime_state.devices)
    monkeypatch.setattr(tasks_router, "tasks", runtime_state.tasks)
    monkeypatch.setattr(task_runtime, "tasks", runtime_state.tasks)
    runtime_state.devices[device_key] = {
        "user_id": user["id"],
        "info": {"hostname": "workstation", "os": "Windows", "machine_guid": "machine-1"},
        "pending": {},
    }

    async def fake_classify_task_complexity(message):
        return ("SIMPLE", "")

    async def fake_process_nl_command(**kwargs):
        return {"answer": "Запомнил: тестовый факт.", "commands": [], "tasks": []}

    monkeypatch.setattr(task_runtime, "classify_task_complexity", fake_classify_task_complexity)
    monkeypatch.setattr(task_runtime, "process_nl_command", fake_process_nl_command)

    created = client.post("/api/chat", headers=headers, json={"message": "remember this", "device_id": "device-1"})
    assert created.status_code == 200
    task = _wait_for_task_status(client, headers, created.json()["task_id"], "done")

    assert "Запомнил" not in task["answer"]
    assert get_user_facts(str(user["id"])) == []

import asyncio
import json
import sqlite3

import pytest

from server import database as db
from server import task_runtime as rt
from server import controller_pipeline as pipeline
from server import python_toolchain as toolchain
from server.routers import tasks as routes
from test_controller_pipeline_budget import _answer_call


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "owners.sqlite3")
    db.init_db()
    return db.create_user("owner-a"), db.create_user("owner-b")


def test_same_id_profiles_updates_deletion_and_metadata_are_owner_scoped(owners):
    a, b = owners
    for user, name in ((a, "alpha"), (b, "bravo")):
        db.upsert_device_profile("same", user["id"], {"hostname": name, "desktop_path": name,
            "activation_summary": {"owner": name}, "python_runtime_summary": {"owner": name}})
    saved_a = db.get_device_profile("same", user_id=a["id"])
    saved_b = db.get_device_profile("same", user_id=b["id"])
    assert saved_a["id"] != saved_b["id"]
    for user, other in ((b, a), (a, b)):
        before = db.get_device_profile("same", user_id=other["id"])
        db.upsert_device_profile("same", user["id"], {"hostname": "updated"})
        db.update_device_activation_summary("same", {"changed": True}, user_id=user["id"])
        db.update_device_python_runtime_summary("same", {"changed": True}, user_id=user["id"])
        assert db.get_device_profile("same", user_id=other["id"]) == before
    assert db.get_device_profile("same", user_id=999999) is None
    assert not db.delete_device_profile("same", user_id=999999)
    assert db.delete_device_profile("same", user_id=a["id"])
    assert db.get_device_profile("same", user_id=b["id"])


def test_legacy_profile_migration_preserves_rows_and_is_idempotent(owners):
    a, b = owners
    db.upsert_device_profile("same", a["id"], {"hostname": "old", "activation_summary": {"x": 1}})
    before = db.get_device_profile("same", user_id=a["id"])
    with db.get_db() as conn:
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='device_profiles'").fetchone()[0]
        legacy = schema.replace("device_profiles", "legacy_profiles", 1).replace("UNIQUE(user_id, device_id)", "UNIQUE(device_id)")
        # Historical schema declared UNIQUE inline.
        legacy = legacy.replace(",\n                UNIQUE(device_id)", "").replace("device_id   TEXT    NOT NULL", "device_id TEXT UNIQUE NOT NULL")
        conn.execute(legacy)
        conn.execute("INSERT INTO legacy_profiles SELECT * FROM device_profiles")
        conn.execute("DROP TABLE device_profiles")
        conn.execute("ALTER TABLE legacy_profiles RENAME TO device_profiles")
    db.init_db()
    db.init_db()
    assert db.get_device_profile("same", user_id=a["id"]) == before
    db.upsert_device_profile("same", b["id"], {"hostname": "new"})
    assert db.get_device_profile("same", user_id=a["id"]) == before
    with db.get_db() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_same_id_online_dispatch_and_api_profiles_are_isolated(client):
    a, b = db.create_user("socket-a"), db.create_user("socket-b")
    seen = []
    class Socket:
        def __init__(self, key): self.key = key
        async def send_text(self, text):
            payload = json.loads(text)["payload"]
            seen.append(self.key)
            rt.devices[self.key]["pending"].pop(payload["id"]).set_result({"returncode": 0})
    for user in (a, b):
        key = f"{user['id']}:same"
        db.upsert_device_profile("same", user["id"], {"hostname": user["name"]})
        rt.devices[key] = {"user_id": user["id"], "info": {}, "pending": {}, "ws": Socket(key)}
        token = client.post("/api/auth", json={"token": user["token"]}).json()["access_token"]
        response = client.get("/api/device_profiles/same", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert user["name"] in response.text
        assert (b if user == a else a)["name"] not in response.text
    async def run():
        await rt.send_command_to_agent(f"{a['id']}:same", "execute_cmd", {"command": "whoami"}, user_id=a["id"])
        with pytest.raises(RuntimeError, match="target_device_not_found"):
            await rt.send_command_to_agent(f"{b['id']}:same", "execute_cmd", {"command": "whoami"}, user_id=a["id"])
    asyncio.run(run())
    assert seen == [f"{a['id']}:same"]


def test_python_cache_does_not_cross_owners():
    toolchain._RECEIPT_CACHE.clear()
    for owner, path in ((1, r"C:\A\python.exe"), (2, r"D:\B\python.exe")):
        toolchain.python_toolchain_from_runtime_summary({"runtime_status": "ok", "venv_python": path,
            "python_version": "3.12.1", "pip_status": "ok"}, device_id="same", user_id=owner)
    assert toolchain.get_cached_python_toolchain({"device_id": "same", "user_id": 1}).interpreter_path == r"C:\A\python.exe"
    assert toolchain.get_cached_python_toolchain({"device_id": "same", "user_id": 2}).interpreter_path == r"D:\B\python.exe"
    assert toolchain.get_cached_python_toolchain({"device_id": "same", "user_id": 3}) is None
    toolchain._RECEIPT_CACHE.clear()


def setup_broadcast(monkeypatch, failures=(), confirmation=False):
    seen, sent = [], []
    for did, desktop in (("a", "/home/alice/Desktop"), ("b", "/home/bob/Desktop")):
        rt.devices[f"1:{did}"] = {"user_id": 1, "info": {"hostname": did, "os": "Linux", "desktop_path": desktop}, "ws": object()}
    rt.tasks["broadcast"] = {"user_id": 1, "status": "running", "results": {}, "modes": {"plan_declined": True}, "chat_id": 1}
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "get_messages", lambda *a, **kw: [{"role": "assistant", "content": "/home/alice/private"}])
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    async def send(target, action, params, **kw):
        if confirmation and not kw.get("skip_confirm"):
            raise RuntimeError("CONFIRM_REQUIRED")
        sent.append((target, params["command"]))
        return {"returncode": 1 if target[-1] in failures else 0, "stdout": target}
    async def process(**kw):
        did = kw["device_id"]
        seen.append(did)
        assert set(kw["all_devices"]) == {did}
        assert kw["chat_history"] == []
        other = "b" if did == "a" else "a"
        with pytest.raises(RuntimeError, match="target_device_not_found"):
            await kw["send_command_fn"](other, "execute_cmd", {"command": "wrong"})
        denied = await kw["device_tool_fn"]("device_get_passport", {"device_id": f"2:{did}"})
        assert denied["status"] == "unavailable"
        command = f"ls {kw['device_info']['desktop_path']}"
        result = await kw["send_command_fn"](did, "execute_cmd", {"command": command})
        return {"answer": "finished", "commands": [{"command": command, "device_id": did, "result": result}]}
    monkeypatch.setattr(rt, "send_command_to_agent", send)
    monkeypatch.setattr(rt, "process_nl_command", process)
    return seen, sent


@pytest.mark.parametrize("failures, overall, terminal", [((), "success", "done"), (("b",), "partial_failure", "failed"), (("a", "b"), "failed", "failed")])
def test_broadcast_independent_context_and_honest_status(monkeypatch, owners, failures, overall, terminal):
    seen, sent = setup_broadcast(monkeypatch, failures)
    asyncio.run(rt.run_nl_task("broadcast", 1, "list desktop", ["1:a", "1:b"], 1))
    assert seen == ["a", "b"]
    assert sent == [("1:a", "ls /home/alice/Desktop"), ("1:b", "ls /home/bob/Desktop")]
    assert rt.tasks["broadcast"]["overall_status"] == overall
    assert rt.tasks["broadcast"]["status"] == terminal


@pytest.mark.parametrize("cancel", [False, True])
def test_broadcast_confirmation_resumes_same_device_or_cancels(monkeypatch, owners, cancel):
    seen, sent = setup_broadcast(monkeypatch, confirmation=True)
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 1})
    async def run():
        work = asyncio.create_task(rt.run_nl_task("broadcast", 1, "list desktop", ["1:a", "1:b"], 1))
        for did in ("a", "b"):
            for _ in range(100):
                if rt.tasks["broadcast"].get("status") == "confirm": break
                await asyncio.sleep(0)
            assert rt.tasks["broadcast"]["confirm_data"]["device_id"] == did
            if cancel:
                await routes.api_cancel_task("broadcast", None)
                break
            await routes.api_confirm_task("broadcast", None)
        await asyncio.wait_for(work, 3)
    asyncio.run(run())
    assert seen == (["a"] if cancel else ["a", "b"])
    assert len(sent) == (0 if cancel else 2)
    assert rt.tasks["broadcast"]["status"] == ("cancelled" if cancel else "done")


def run_plan(monkeypatch, steps, devices):
    seen, states = [], {}
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: 1)
    monkeypatch.setattr(pipeline.db, "update_step", lambda task, idx, status, **kw: states.update({idx: status}))
    monkeypatch.setattr(pipeline.db, "finish_task", lambda *a: None)
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [])
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    async def completion(**kw):
        if kw["phase"] == "pipeline.plan":
            return {"choices": [{"message": {"content": json.dumps({"steps": steps})}}]}
        return {"choices": [{"message": {"tool_calls": [_answer_call("final", "Итог")]}}]}
    async def send(target, action, params):
        seen.append((target, params["command"]))
        return {"returncode": 0}
    async def worker(**kw):
        target = kw["step"]["device_id"]
        wrong = "b" if target == "a" else "a"
        with pytest.raises(RuntimeError, match="target_device_not_found"):
            await kw["send_command_fn"](wrong, "execute_cmd", {"command": "wrong"})
        await kw["send_command_fn"](target, "execute_cmd", {"command": kw["step"]["instruction"]})
        return {"status": "ok", "answer": "done", "commands": []}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", worker)
    result = asyncio.run(pipeline.process_pipeline_subagents(user_message="tasks", device_id="a",
        device_info={"os": "Linux"}, all_devices=devices, send_command_fn=send, get_file_link_fn=lambda *a: "",
        chat_history=[], user_id=1, load_llm_config_fn=lambda: {"model": "mock"}, pick_model_fn=lambda *a: "mock",
        chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
    return result, seen, states


@pytest.mark.parametrize("target, extra", [("missing", {}), ("2:a", {}), ("b", {"b": {"user_id": 2, "ws": object()}}), ("b", {"b": {"user_id": 1, "ws": None}})])
def test_plan_invalid_offline_foreign_target_never_dispatches(monkeypatch, target, extra):
    result, seen, states = run_plan(monkeypatch, [{"instruction": "do", "device_id": target}], {"a": {"user_id": 1, "ws": object()}, **extra})
    assert seen == []
    assert states[0] == "failed"
    assert result["task_receipt"]["task_status"] == "failed"


def test_plan_routes_each_step_exactly_and_allows_missing_target(monkeypatch):
    result, seen, states = run_plan(monkeypatch, [{"instruction": "task-a"}, {"instruction": "task-b", "device_id": "b"}],
        {"a": {"user_id": 1, "ws": object()}, "b": {"user_id": 1, "ws": object()}})
    assert seen == [("a", "task-a"), ("b", "task-b")]
    assert states == {0: "done", 1: "done"}
    assert result["task_receipt"]["task_status"] == "completed"


def test_websocket_registration_same_id_preserves_both_profiles(client):
    import time
    a, b = db.create_user("register-a"), db.create_user("register-b")
    def wait_profile(user, name):
        for _ in range(100):
            profile = db.get_device_profile("same", user_id=user["id"])
            if profile and profile["hostname"] == name:
                return profile
            time.sleep(0.01)
        raise AssertionError("registration did not persist")
    with client.websocket_connect(f"/ws/same?user_token={a['token']}") as wa:
        wa.send_json({"type": "register", "payload": {"hostname": "A", "activation_summary": {"owner": "A"}}})
        before = wait_profile(a, "A")
        with client.websocket_connect(f"/ws/same?user_token={b['token']}") as wb:
            wb.send_json({"type": "register", "payload": {"hostname": "B"}})
            other = wait_profile(b, "B")
            assert db.get_device_profile("same", user_id=a["id"]) == before
            assert other["activation_summary"] is None
            wa.send_json({"type": "register", "payload": {"hostname": "A2"}})
            wait_profile(a, "A2")
            assert db.get_device_profile("same", user_id=b["id"]) == other


@pytest.mark.parametrize("failures, overall", [((), "success"), (("b",), "partial_failure"), (("a", "b"), "failed")])
def test_raw_command_broadcast_aggregates_failures(client, monkeypatch, failures, overall):
    user = db.create_user("raw-broadcast")
    db.set_user_plan(user["id"], "pro")
    token = client.post("/api/auth", json={"token": user["token"]}).json()["access_token"]
    for did in ("a", "b"):
        rt.devices[f"{user['id']}:{did}"] = {"user_id": user["id"], "info": {}, "ws": object()}
    async def send(target, action, params, **kw):
        return {"returncode": 1 if target[-1] in failures else 0}
    monkeypatch.setattr(routes, "send_command_to_agent", send)
    response = client.post("/api/raw_command", headers={"Authorization": f"Bearer {token}"},
                           json={"command": "whoami", "broadcast": True})
    assert response.status_code == 200
    assert response.json()["overall_status"] == overall


@pytest.mark.parametrize("invalid", [0, False, [], {}, "", "   "])
def test_plan_malformed_explicit_target_is_not_a_default(invalid):
    with pytest.raises(ValueError, match="target_device_not_found"):
        pipeline.normalize_pipeline_plan({"steps": [{"instruction": "do", "device_id": invalid}]}, "goal", "a")
    with pytest.raises(ValueError, match="target_device_not_found"):
        pipeline.validate_pipeline_step_device({"device_id": invalid}, "a", {"a": {}})

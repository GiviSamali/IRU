"""Synthetic restart recovery; no server process or device is started."""
import asyncio
import json
import sqlite3
import time

import pytest

from server import database as db
from server.worker_scheduler import WorkerScheduler, init_worker_storage, owned_job


@pytest.fixture
def queue_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "restart.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("restart-owner")
    owner["chat_id"] = db.create_chat(owner["id"], "recovery")["id"]
    return owner


@pytest.mark.parametrize("source_state", ["running", "queued", "waiting_dependency"])
@pytest.mark.parametrize("dispatch_marker", [0, 1.0])
def test_restart_never_replays_dispatched_source_but_resumes_independent_queued_job(queue_storage, monkeypatch, source_state, dispatch_marker):
    owner = queue_storage

    async def scenario():
        original = WorkerScheduler()
        monkeypatch.setattr(original, "ensure_runner", lambda owner: None)
        result = await original.submit_goal({"user_id":owner["id"], "chat_id":owner["chat_id"],
            "message":"Research, document, and independent file", "device_ids":[], "kind":"worker",
            "created_at":time.time()}, [
                {"key":"research", "objective":"research"},
                {"key":"document", "objective":"document", "depends_on":["research"]},
                {"key":"independent", "objective":"independent"}], request_key="restart")
        source, consumer, independent = result["tasks"]
        with db.get_db() as c:
            payload = json.loads(c.execute("SELECT payload FROM worker_jobs WHERE task_id=?", (source["task_id"],)).fetchone()[0])
            payload["dispatch_started_at"] = dispatch_marker
            c.execute("UPDATE worker_jobs SET state=?,payload=? WHERE task_id=?", (source_state, json.dumps(payload), source["task_id"]))

        executed = []
        async def execute(task):
            executed.append(task["proposed_objective"])
            task.update(status="cancelled", answer="synthetic end", commands=[])
        restored = WorkerScheduler(execute)
        monkeypatch.setattr(restored, "acquire_instance", lambda: True)
        scheduled = []
        monkeypatch.setattr(restored, "ensure_runner", lambda owner: scheduled.append(owner))
        try:
            await restored.recover()
            assert owned_job(source["task_id"], owner["id"])["state"] == "unknown"
            assert owned_job(consumer["task_id"], owner["id"])["state"] == "waiting_dependency"
            assert owned_job(independent["task_id"], owner["id"])["state"] == "running"
            assert scheduled == [owner["id"]]
            assert executed == []
            WorkerScheduler.ensure_runner(restored, owner["id"])
            await restored.runners[owner["id"]]
            assert executed == ["independent"]
            assert owned_job(source["task_id"], owner["id"])["state"] == "unknown"
            with db.get_db() as c:
                assert c.execute("SELECT COUNT(*) FROM worker_results WHERE task_id=?", (source["task_id"],)).fetchone()[0] == 0
        finally:
            await restored.shutdown()

    asyncio.run(scenario())


def test_sqlite_enforces_default_capacity_even_without_scheduler_admission(queue_storage):
    owner = queue_storage

    def insert(c, task_id):
        c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (
            task_id, owner["id"], owner["chat_id"], "running", "{}", None, None, 1, 1, None))

    with db.get_db() as c:
        insert(c, "first")
    with pytest.raises(sqlite3.IntegrityError, match="worker_capacity_exceeded"):
        with db.get_db() as c:
            insert(c, "second")
    assert owned_job("first", owner["id"])["state"] == "running"
    assert owned_job("second", owner["id"]) is None


@pytest.mark.parametrize("dispatch_marker", [0, 1.0])
def test_cancel_of_marked_queue_row_keeps_unknown_outcome_and_evidence(queue_storage, dispatch_marker):
    owner = queue_storage
    original_commands = [{"tool_name":"execute_cmd", "step_id":"step_1",
        "status":"unknown", "result":{"error":"agent_disconnected"}}]
    payload = {"task_id":"marked-queue", "user_id":owner["id"], "chat_id":owner["chat_id"],
        "message":"Original action", "status":"queued", "worker_id":"worker-1",
        "device_ids":[], "commands":original_commands, "dispatch_started_at":dispatch_marker}
    with db.get_db() as c:
        c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (
            payload["task_id"], owner["id"], owner["chat_id"], "queued", json.dumps(payload),
            None, None, 1, 1, None))
    cancelled = asyncio.run(WorkerScheduler().cancel(payload["task_id"], owner["id"]))
    assert cancelled["worker_report"]["status"] == "unknown"
    assert cancelled["commands"] == original_commands
    saved = owned_job(payload["task_id"], owner["id"])
    assert saved["state"] == "unknown"
    assert json.loads(saved["payload"])["commands"] == original_commands


@pytest.mark.parametrize("dispatch_marker", [0, 1.0])
def test_missing_dispatch_coroutine_never_causes_same_process_replay(queue_storage, monkeypatch, dispatch_marker):
    owner = queue_storage

    async def scenario():
        executed = []
        async def execute(task):
            executed.append(task["proposed_objective"])
            task.update(status="cancelled", answer="synthetic end", commands=[])
        scheduler = WorkerScheduler(execute)
        monkeypatch.setattr(scheduler, "ensure_runner", lambda owner: None)
        result = await scheduler.submit_goal({"user_id":owner["id"], "chat_id":owner["chat_id"],
            "message":"Two independent results", "device_ids":[], "kind":"worker", "created_at":time.time()}, [
                {"key":"a", "objective":"possibly dispatched"},
                {"key":"b", "objective":"never dispatched"}], request_key="orphan")
        first = result["tasks"][0]
        with db.get_db() as c:
            row = c.execute("SELECT payload FROM worker_jobs WHERE task_id=?", (first["task_id"],)).fetchone()
            payload = json.loads(row["payload"])
            payload["dispatch_started_at"] = dispatch_marker
            c.execute("UPDATE worker_jobs SET payload=? WHERE task_id=?", (json.dumps(payload), first["task_id"]))
        try:
            WorkerScheduler.ensure_runner(scheduler, owner["id"])
            await scheduler.runners[owner["id"]]
            assert executed == ["never dispatched"]
            row = owned_job(first["task_id"], owner["id"])
            assert row["state"] == "unknown"
            assert json.loads(row["payload"])["worker_error_code"] == "worker_dispatch_interrupted"
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())

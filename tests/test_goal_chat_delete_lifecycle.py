"""Regression: deleting a chat must not break Tasks or execute orphaned work.

All tests use an isolated SQLite database and synthetic Workers; no real device
or model calls and no production data.
"""
import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from server import database as db
from server import worker_scheduler as queue
from server.routers import chats as chats_api, tasks as tasks_api
from server.runtime_state import tasks


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "deleted-chat.sqlite")
    db.init_db()
    queue.init_worker_storage()
    owner = db.create_user("chat-delete-regression")
    chat = db.create_chat(owner["id"], "will be deleted")["id"]
    other = db.create_chat(owner["id"], "retained chat")["id"]
    monkeypatch.setattr(chats_api, "get_current_user", lambda request: owner)
    monkeypatch.setattr(tasks_api, "get_current_user", lambda request: owner)
    return owner, chat, other


def put_job(owner, chat, task_id, state, *, goal_id=None, dispatched=False):
    payload = {"task_id": task_id, "user_id": owner["id"], "chat_id": chat,
               "message": task_id, "device_ids": [], "status": state,
               "worker_id": "worker-1", "created_at": time.time()}
    if goal_id:
        payload["goal_id"] = goal_id
    if dispatched:
        payload["dispatch_started_at"] = 1.0
    with db.get_db() as connection:
        connection.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (task_id, owner["id"], chat, state, json.dumps(payload),
                            None, None, time.time(), time.time(), None))


def test_legacy_deleted_chat_no_longer_breaks_operations_or_exposes_details(context):
    owner, deleted, retained = context
    # Simulate historical data left by the previous implementation.
    put_job(owner, deleted, "legacy-orphan", "queued", goal_id="missing-goal")
    put_job(owner, retained, "visible-task", "queued")
    assert db.delete_chat(deleted, owner["id"])

    async def inspect():
        response = await tasks_api.api_operations(object())
        assert response["status"] == "ok"
        assert [item["task_id"] for item in response["operations"]] == ["visible-task"]
        listed = await tasks_api.api_list_tasks(object())
        assert all(item["chat_id"] != deleted for item in listed["tasks"])
        with pytest.raises(HTTPException) as caught:
            await tasks_api.api_get_task("legacy-orphan", object())
        assert caught.value.status_code == 404

    asyncio.run(inspect())
    # Historical evidence is retained, not deleted in order to make the list work.
    assert queue.owned_job("legacy-orphan", owner["id"]) is not None


def test_broken_goal_reference_does_not_hide_other_tasks(context):
    owner, chat, retained = context
    put_job(owner, chat, "bad-goal-member", "queued", goal_id="missing-goal")
    put_job(owner, retained, "good-member", "queued")

    response = asyncio.run(tasks_api.api_operations(object()))
    assert response["status"] == "ok"
    by_id = {item["task_id"]: item for item in response["operations"]}
    assert set(by_id) == {"bad-goal-member", "good-member"}
    assert by_id["bad-goal-member"]["goal_title"] is None
    assert by_id["bad-goal-member"]["goal_task_count"] == 1


def test_delete_chat_stops_pending_and_requests_stop_of_running_work(context, monkeypatch):
    owner, deleted, retained = context
    async def scenario():
        running = asyncio.Event()
        finish = asyncio.Event()
        started = []

        async def execute(task):
            started.append(task["task_id"])
            if task["chat_id"] == deleted:
                running.set()
                await finish.wait()
            task.update(status="cancelled" if task.get("cancel_requested") else "failed",
                        answer="synthetic terminal", commands=[])

        scheduler = queue.WorkerScheduler(execute)
        monkeypatch.setattr(queue, "scheduler", scheduler)
        try:
            template = {"user_id":owner["id"], "chat_id":deleted, "message":"two results",
                        "device_ids":[], "kind":"worker", "created_at":time.time()}
            admitted = await scheduler.submit_goal(template, [
                {"key":"running","objective":"running"},
                {"key":"pending","objective":"pending"}], request_key="will-delete")
            first, second = [item["task_id"] for item in admitted["tasks"]]
            await asyncio.wait_for(running.wait(), timeout=5)
            other = await scheduler.submit_goal({**template,"chat_id":retained,"message":"another chat"},
                [{"key":"separate","objective":"separate"}], request_key="keep")
            separate = other["tasks"][0]["task_id"]

            result = await chats_api.api_delete_chat(deleted, object())
            assert result == {"status":"ok","deleted":True}
            assert db.get_chat(deleted,owner["id"]) is None
            assert tasks[first]["cancel_requested"] is True
            assert queue.owned_job(second,owner["id"])["state"] == "cancelled"
            assert queue.owned_job(separate,owner["id"])["state"] == "queued"
            assert first in started and second not in started
            visible = await tasks_api.api_operations(object())
            assert all(item["chat_id"] == retained for item in visible["operations"])

            finish.set()
            await asyncio.wait_for(scheduler.runners[owner["id"]], timeout=8)
            assert queue.owned_job(first,owner["id"])["state"] == "cancelled"
            assert queue.owned_job(separate,owner["id"])["state"] != "cancelled"
            assert second not in started
        finally:
            finish.set()
            await scheduler.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("dispatched", [False, True])
def test_recovery_never_restarts_deleted_chat_jobs(context, monkeypatch, dispatched):
    owner, deleted, retained = context
    put_job(owner, deleted, "orphan-before-restart", "queued", dispatched=dispatched)
    put_job(owner, retained, "keep-before-restart", "queued")
    assert db.delete_chat(deleted, owner["id"])

    async def scenario():
        scheduler = queue.WorkerScheduler()
        monkeypatch.setattr(scheduler, "acquire_instance", lambda: True)
        started = []
        monkeypatch.setattr(scheduler, "ensure_runner", lambda user_id: started.append(user_id))
        try:
            await scheduler.recover()
            orphan = queue.owned_job("orphan-before-restart", owner["id"])
            assert orphan["state"] == ("unknown" if dispatched else "cancelled")
            assert ("dispatch_started_at" in json.loads(orphan["payload"])) is dispatched
            assert queue.owned_job("keep-before-restart", owner["id"])["state"] == "running"
            assert started == [owner["id"]]
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())

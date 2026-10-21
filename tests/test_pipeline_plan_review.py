"""The former PLAN review contract is replaced by admission and pending-task CAS."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from server import database as db, orchestrator as orch
from server.routers import tasks as routes
from server.runtime_state import devices
from server.worker_scheduler import WorkerScheduler, init_worker_storage, goal_view


@pytest.fixture
def queue_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "review-migration.sqlite")
    db.init_db()
    init_worker_storage()
    user = db.create_user("review-migration")
    user["chat_id"] = db.create_chat(user["id"], "goal")["id"]
    devices[f"{user['id']}:pc"] = {"user_id":user["id"], "ws":object(), "info":{}}
    return user


async def until(predicate):
    for _ in range(200):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError("Scheduler did not start the accepted task")


def test_explicit_multi_result_request_is_admitted_without_plan_review(queue_owner, monkeypatch):
    owner = queue_owner

    async def scenario():
        gate = asyncio.Event()
        started = []

        async def execute(task):
            started.append(task["task_id"])
            await gate.wait()
            task.update(status="cancelled", answer="synthetic end", commands=[])

        scheduler = WorkerScheduler(execute)
        monkeypatch.setattr(routes, "scheduler", scheduler)
        async def decide(*args, **kwargs):
            return orch.Decision(intent="delegate", objective="Three independent descriptions",
                target_device_ids=["pc"], execution_mode="simple", assignments=[
                    {"key":key, "objective":f"Create description {key}"} for key in ("a","b","c")]), {}
        monkeypatch.setattr(orch, "decide", decide)
        cmd = SimpleNamespace(request_id="three", message="Create three separate descriptions", device_id="pc", modes={}, broadcast=False)

        async def delegate(choice, request_key):
            return await routes.submit_worker(owner, owner["chat_id"], cmd.message, [f"{owner['id']}:pc"], {},
                request_key=request_key, objective=choice.objective,
                assignments=[item.model_dump() for item in choice.assignments])
        try:
            response = await orch.run_turn(cmd, owner, owner["chat_id"], delegate)
            assert len(response["worker_task_ids"]) == 3
            goal = goal_view(response["goal_id"], owner["id"], owner["chat_id"])
            assert len(goal["tasks"]) == 3 and all(not task["dependencies"] for task in goal["tasks"])
            await until(lambda: len(started) == 1)
            assert all(task["status"] != "waiting_confirmation" for task in goal["tasks"])
        finally:
            gate.set()
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_pending_revision_cannot_change_running_sibling_or_accept_stale_edit(queue_owner):
    owner = queue_owner

    async def scenario():
        gate = asyncio.Event()
        started = []
        async def execute(task):
            started.append(task["task_id"])
            await gate.wait()
            task.update(status="cancelled", commands=[], answer="synthetic end")
        scheduler = WorkerScheduler(execute)
        try:
            result = await scheduler.submit_goal({"user_id":owner["id"], "chat_id":owner["chat_id"],
                "message":"Create two documents", "device_ids":[f"{owner['id']}:pc"],
                "created_at":time.time(), "kind":"worker"}, [
                    {"key":"a", "objective":"Document A"}, {"key":"b", "objective":"Document B"}], request_key="two")
            first, pending = result["tasks"]
            await until(lambda: started == [first["task_id"]])
            edited = scheduler.amend_pending(pending["task_id"], owner["id"], owner["chat_id"],
                expected_revision=1, human_request="Shorten B", objective="Short document B")
            assert edited["revision"] == 2
            assert first["proposed_objective"] == "Document A"
            with pytest.raises(ValueError, match="task_revision_conflict"):
                scheduler.amend_pending(pending["task_id"], owner["id"], owner["chat_id"],
                    expected_revision=1, human_request="stale", objective="stale")
            with pytest.raises(ValueError, match="task_already_dispatched"):
                scheduler.amend_pending(first["task_id"], owner["id"], owner["chat_id"],
                    expected_revision=1, human_request="late", objective="late")
        finally:
            gate.set()
            await scheduler.shutdown()
    asyncio.run(scenario())
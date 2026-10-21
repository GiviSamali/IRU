"""Scheduler isolation scenarios. Execution is synthetic, not a device LIVE check."""
import asyncio
import time

import pytest

from server import database as db
from server.worker_scheduler import WorkerScheduler, init_worker_storage, owned_job
from server.runtime_state import tasks


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "parallel.sqlite")
    db.init_db()
    init_worker_storage()
    user = db.create_user("parallel-owner")
    user["chat_id"] = db.create_chat(user["id"], "parallel")["id"]
    yield user
    for tid in [tid for tid, task in tasks.items() if task.get("user_id") == user["id"]]:
        tasks.pop(tid, None)


def template(owner, device=False):
    return {"user_id":owner["id"], "chat_id":owner["chat_id"], "message":"research and two results",
            "device_ids":[f"{owner['id']}:pc"] if device else [], "kind":"worker",
            "created_at":time.time(), "modes":{}}


def complete(task):
    text = "Saved source material with provenance."
    task.update(status="done", answer=text, commands=[
        {"tool_name":"web_search", "step_id":"source-1", "status":"success",
         "device_id":"server", "result":{"status":"success", "results":[
             {"url":"https://example.org", "text":"Source material", "date":"2026-10-20"}]}},
        {"tool_name":"answer.text", "status":"terminal", "result":{
            "answer_type":"grounded_report", "text":text, "basis":["source-1"],
            "self_check":{"depends_on_current_external_state":True, "claims_completed_action":False,
                          "has_sufficient_evidence":True, "missing_evidence_question":""}}}],
        task_receipt={"answer_source":"audited_terminal", "task_status":"completed",
                      "goal_completed":True, "final_verification_status":"verified"})


async def until(predicate):
    for _ in range(300):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError("Scheduler did not reach expected state")


@pytest.mark.parametrize("split_devices", [False, True])
def test_two_consumers_share_one_frozen_source_and_distinct_workers(owner, split_devices):
    async def scenario():
        source_gate, result_gate = asyncio.Event(), asyncio.Event()
        started, snapshots, worker_ids = [], [], []

        async def execute(task):
            started.append(task["proposed_objective"])
            if task["proposed_objective"] == "research":
                await source_gate.wait()
            else:
                snapshots.append(task["pinned_dependency_results"][0]["sha256"])
                worker_ids.append(task["worker_id"])
                await result_gate.wait()
            complete(task)

        scheduler = WorkerScheduler(execute, max_active_workers=2)
        try:
            base = template(owner)
            assignments = [
                {"key":"source", "objective":"research"},
                {"key":"word", "objective":"word", "depends_on":["source"]},
                {"key":"slides", "objective":"slides", "depends_on":["source"]}]
            if split_devices:
                base["device_ids"] = [f"{owner['id']}:a", f"{owner['id']}:b"]
                assignments[0]["target_device_ids"] = []
                assignments[1]["target_device_ids"] = [base["device_ids"][0]]
                assignments[2]["target_device_ids"] = [base["device_ids"][1]]
            result = await scheduler.submit_goal(base, assignments, request_key="shared")
            if split_devices:
                assert result["tasks"][0]["device_ids"] == []
                assert result["tasks"][1]["device_ids"] == [base["device_ids"][0]]
                assert result["tasks"][2]["device_ids"] == [base["device_ids"][1]]
            await until(lambda: started == ["research"])
            assert all(owned_job(task["task_id"], owner["id"])["state"] == "waiting_dependency" for task in result["tasks"][1:])
            source_gate.set()
            await until(lambda: len(started) == 3)
            assert started.count("research") == 1
            assert len(set(snapshots)) == 1
            assert set(worker_ids) == {"worker-1", "worker-2"}
            result_gate.set()
            await scheduler.runners[owner["id"]]
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())


def test_same_device_is_exclusive_even_with_capacity_two(owner):
    async def scenario():
        gate = asyncio.Event()
        started = []

        async def execute(task):
            started.append(task["proposed_objective"])
            await gate.wait()
            complete(task)

        scheduler = WorkerScheduler(execute, max_active_workers=2)
        try:
            await scheduler.submit_goal(template(owner, device=True), [
                {"key":"a", "objective":"a"}, {"key":"b", "objective":"b"}], request_key="exclusive")
            await until(lambda: started == ["a"])
            await asyncio.sleep(.15)
            assert started == ["a"]
            gate.set()
            await scheduler.runners[owner["id"]]
            assert started == ["a", "b"]
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())


def test_capacity_defaults_to_one_and_rejects_invalid_configuration():
    assert WorkerScheduler().max_active_workers == 1
    for value in (0, 5, True, "2"):
        with pytest.raises(ValueError, match="invalid_worker_capacity"):
            WorkerScheduler(max_active_workers=value)


@pytest.mark.parametrize("source_succeeds", [False, True])
def test_fifo_uses_admission_order_only_among_ready_jobs(owner, source_succeeds):
    async def scenario():
        release=asyncio.Event()
        started=[]
        source_hashes=[]
        async def execute(task):
            objective=task["proposed_objective"]
            started.append(objective)
            if objective=="source":
                await release.wait()
                if not source_succeeds:
                    task.update(status="failed",answer="Synthetic source failed",commands=[])
                    return
            if task.get("pinned_dependency_results"):
                source_hashes.append(task["pinned_dependency_results"][0]["sha256"])
            complete(task)
        scheduler=WorkerScheduler(execute)
        try:
            admitted=await scheduler.submit_goal(template(owner),[
                {"key":"source","objective":"source"},
                {"key":"first","objective":"first dependent","depends_on":["source"]},
                {"key":"independent","objective":"independent"},
                {"key":"last","objective":"last dependent","depends_on":["source"]}],request_key="ready-fifo")
            await until(lambda:started==["source"])
            assert owned_job(admitted["tasks"][1]["task_id"],owner["id"])["state"]=="waiting_dependency"
            assert owned_job(admitted["tasks"][2]["task_id"],owner["id"])["state"]=="queued"
            release.set()
            await scheduler.runners[owner["id"]]
            assert started==(["source","first dependent","independent","last dependent"]
                             if source_succeeds else ["source","independent"])
            if source_succeeds:
                assert len(source_hashes)==2 and len(set(source_hashes))==1
            else:
                assert source_hashes==[]
                assert all(owned_job(admitted["tasks"][index]["task_id"],owner["id"])["state"]=="waiting_dependency"
                           for index in (1,3))
        finally:
            release.set()
            await scheduler.shutdown()
    asyncio.run(scenario())

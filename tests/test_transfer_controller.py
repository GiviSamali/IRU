"""Real common-Worker dispatch plus synthetic queue dependency tests; no device LIVE."""
import asyncio
import json
import time

import pytest

from server import database as db
from server.controller_tools import WORKER_TOOLS, NON_PIPELINE_TOOLS
from server.worker_scheduler import WorkerScheduler, init_worker_storage, owned_job
from server.worker_reports import build_worker_report
from test_controller_trust import _run_non_pipeline_case, _answer_call
from test_goal_parallel_scheduler import complete

ARGS = {"source_device_id":"a", "target_device_id":"b", "source_path":"file", "target_directory":"desktop"}
SUCCESS = {"status":"success", "sha256_verified":True, "filename":"file", "target_device":"b",
           "source_device":"a", "path":"target/file", "target_path":"target/file"}


@pytest.mark.parametrize("success", [True, False])
def test_common_worker_dispatches_transfer_and_preserves_actual_result(monkeypatch, success):
    monkeypatch.setattr(db, "get_device_profile", lambda *a, **kw: None)
    calls = []
    outcome = SUCCESS if success else {"status":"failed", "error":"target_offline"}
    async def send(device, action, arguments):
        calls.append((action, arguments))
        return outcome
    result = _run_non_pipeline_case([
        {"choices":[{"message":{"tool_calls":[{"id":"transfer", "function":{
            "name":"transfer_file", "arguments":json.dumps(ARGS)}}]}}]},
        {"choices":[{"message":{"tool_calls":[_answer_call("end", "Recorded result")]}}]},
    ], send_command_fn=send)
    assert calls == [("transfer_file", ARGS)]
    entry = next(item for item in result["commands"] if item.get("tool_name") == "transfer_file")
    assert entry["result"] == outcome
    assert WORKER_TOOLS is NON_PIPELINE_TOOLS


def test_transfer_receipt_alone_is_not_a_whole_goal_report():
    report = build_worker_report({"task_id":"transfer", "worker_id":"worker-1", "status":"done",
        "message":"Prepare, transfer and open the document", "device_ids":["1:a","1:b"],
        "answer":"Everything done", "commands":[{"step_id":"transfer-1", "tool_name":"transfer_file",
            "status":"success", "device_id":"a", "result":SUCCESS}]})
    assert report["goal_completed"] is False


@pytest.mark.parametrize("failure", [False, True])
def test_create_transfer_open_dependency_uses_queue_and_never_opens_after_failure(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "transfer-graph.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("transfer-graph")
    chat = db.create_chat(owner["id"], "graph")["id"]
    async def scenario():
        executed = []
        async def execute(task):
            name = task["proposed_objective"]
            executed.append(name)
            # Synthetic verified inputs exercise queue mechanics only; the real
            # transfer tool dispatch and its receipts are checked separately above.
            if name == "transfer" and failure:
                task.update(status="failed", answer="Transfer failed", commands=[])
            else:
                complete(task)
        scheduler = WorkerScheduler(execute)
        try:
            result = await scheduler.submit_goal({"user_id":owner["id"], "chat_id":chat,
                "message":"Create, transfer and open", "device_ids":[], "kind":"worker", "created_at":time.time()}, [
                    {"key":"create", "objective":"create"},
                    {"key":"transfer", "objective":"transfer", "depends_on":["create"]},
                    {"key":"open", "objective":"open", "depends_on":["transfer"]}], request_key="chain")
            await scheduler.runners[owner["id"]]
            assert executed == (["create","transfer"] if failure else ["create","transfer","open"])
            open_state = owned_job(result["tasks"][2]["task_id"], owner["id"])["state"]
            assert open_state == ("waiting_dependency" if failure else "success")
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())
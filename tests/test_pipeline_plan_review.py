import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from server import controller_pipeline as pipeline
from server.pipeline_plan_review import review_pipeline_plan
from server.runtime_state import tasks, cleanup_old_tasks, TASK_TTL
from server.routers import tasks as routes
from test_controller_pipeline_budget import _answer_call


def test_review_revisions_wait_before_tools_and_preserve_request(monkeypatch):
    tid = "review-flow"
    monkeypatch.setitem(tasks, tid, {"task_id": tid, "user_id": 1, "status": "running", "created_at": time.time()})
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": request or 1})
    requests, workers, created = [], [], []
    original = "Создай PPTX, Word и Excel на рабочем столе"
    async def completion(**kwargs):
        if kwargs["phase"] == "pipeline.final":
            payload = json.loads(kwargs["messages"][-1]["content"])
            assert payload["original_request"] == original
            assert payload["approved_plan"]["steps"][0]["title"] == "Версия 3"
            return {"choices": [{"message": {"tool_calls": [_answer_call("final", "Результат")]}}]}
        assert kwargs["phase"] == "pipeline.plan"
        requests.append(kwargs["messages"])
        if len(requests) > 1:
            feedback = json.loads(kwargs["messages"][-1]["content"])
            assert feedback["requested_changes"] == f"Изменение {len(requests) - 1}"
            assert feedback["current_unexecuted_plan"]["steps"][0]["title"] == f"Версия {len(requests) - 1}"
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"steps": [
            {"title": f"Версия {len(requests)}", "instruction": "Создать весь согласованный пакет"}]})}}]}
    async def worker(**kwargs):
        workers.append(kwargs)
        assert len(requests) == 3
        assert original in kwargs["overall_goal"] and "Версия 3" in kwargs["overall_goal"]
        return {"status": "ok", "answer": "Результат", "commands": []}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", worker)
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: created.append(kw) or 1)
    monkeypatch.setattr(pipeline.db, "update_step", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, "finish_task", lambda *a: None)
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [])
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    async def send(*a): pytest.fail("no real device calls")
    async def scenario():
        execution = asyncio.create_task(pipeline.process_pipeline_subagents(
            user_message=original, device_id="pc", device_info={"os": "Windows"}, all_devices={},
            send_command_fn=send, get_file_link_fn=lambda *a: "", chat_history=[], poll_task_id=tid,
            load_llm_config_fn=lambda: {"model": "mock-model"}, pick_model_fn=lambda *a: "mock-model",
            chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
        previous = None
        try:
            for version in range(1, 4):
                for _ in range(100):
                    if tasks[tid].get("status") == "confirm": break
                    await asyncio.sleep(0)
                review = tasks[tid]["plan_review"]
                assert review["revision"] != previous
                assert review["speech"].endswith("Хотите что-то изменить?")
                assert not workers and not created and not execution.done()
                body = routes.PlanReviewBody(revision=review["revision"], action="approve")
                with pytest.raises(HTTPException) as foreign:
                    await routes.api_review_plan(tid, body, 2)
                assert foreign.value.status_code == 404
                with pytest.raises(HTTPException) as blank:
                    await routes.api_review_plan(tid, routes.PlanReviewBody(revision=review["revision"], action="revise", changes=" "), None)
                assert blank.value.status_code == 400
                if previous:
                    with pytest.raises(HTTPException) as stale:
                        await routes.api_review_plan(tid, routes.PlanReviewBody(revision=previous, action="approve"), None)
                    assert stale.value.status_code == 409
                if version < 3:
                    body = routes.PlanReviewBody(revision=review["revision"], action="revise", changes=f"Изменение {version}")
                await routes.api_review_plan(tid, body, None)
                with pytest.raises(HTTPException) as duplicate:
                    await routes.api_review_plan(tid, body, None)
                assert duplicate.value.status_code == 409
                previous = review["revision"]
            result = await asyncio.wait_for(execution, 2)
            assert result["task_receipt"]["task_status"] == "completed"
        finally:
            if not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
    asyncio.run(scenario())
    assert len(workers) == len(created) == 1
    assert "plan_review" not in tasks[tid] and "_pipeline_plan_future" not in tasks[tid]


@pytest.mark.parametrize("action", ["cancel", "deny", "expire"])
def test_review_cancel_or_expiry_releases_waiter(monkeypatch, action):
    tid = "cancel-review"
    monkeypatch.setitem(tasks, tid, {"task_id": tid, "user_id": 1, "status": "running", "created_at": time.time()})
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 1})
    async def scenario():
        pending = asyncio.create_task(review_pipeline_plan(tid, {"goal": "goal", "steps": [{"title": "document"}]}))
        await asyncio.sleep(0)
        assert not pending.done() and tasks[tid]["status"] == "confirm"
        if action == "expire":
            tasks[tid]["created_at"] = time.time() - TASK_TTL - 1
            cleanup_old_tasks()
        elif action == "cancel":
            await routes.api_cancel_task(tid, None)
        else:
            await routes.api_deny_task(tid, None)
        assert await asyncio.wait_for(pending, 1) == {"action": "cancel"}
    asyncio.run(scenario())

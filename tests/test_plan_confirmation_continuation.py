"""Regression for production PLAN disappearing after an approved Desktop probe."""
import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from server import controller_pipeline as pipeline, task_runtime as runtime
from server.routers import tasks as routes
from test_controller_pipeline_budget import _execute_call
from test_pipeline_recovery_receipt import _answer_text_call


REQUEST = "Найди аналоги ИРУ в интернете и создай презентацию, Word и Excel в одной папке на рабочем столе"


@pytest.mark.parametrize("raw", [None, {}, {"steps": []}, {"steps": ["prepare", None, "verify"]},
                                {"steps": ["prepare", "", "verify"]}])
def test_invalid_plan_cannot_silently_become_a_smaller_task(raw):
    with pytest.raises(ValueError):
        pipeline.normalize_pipeline_plan(raw, REQUEST, "pc")


def test_orphaned_pipeline_confirmation_never_uses_single_command_completion(monkeypatch):
    monkeypatch.setitem(runtime.tasks, "orphan-plan", {
        "user_id": 1, "status": "confirm", "modes": {"pipeline": True}})
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 1})
    async def send(*args, **kwargs):
        pytest.fail("must not execute a command without its PLAN continuation")
    monkeypatch.setattr(routes, "send_command_to_agent", send)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.api_confirm_task("orphan-plan", None))
    assert exc.value.status_code == 409
    assert runtime.tasks["orphan-plan"]["status"] == "confirm"


@pytest.mark.parametrize("content,finish", [
    ('{"steps": ["prepare", null, "create"]}', "stop"),
    ('{"steps": ["prepare"]}', "length"),
    ('{"steps": ["prepare",', "length"),
])
def test_invalid_or_truncated_plan_fails_before_any_worker(monkeypatch, content, finish):
    calls = []
    async def completion(**kwargs):
        calls.append(kwargs["phase"])
        return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    async def unexpected(*args, **kwargs):
        pytest.fail("invalid plan must not execute")
    monkeypatch.setattr(pipeline, "run_pipeline_worker", unexpected)
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kwargs: pytest.fail("no partial UI plan"))
    result = asyncio.run(pipeline.process_pipeline_subagents(
        user_message=REQUEST, device_id="pc", device_info={"os": "Windows"}, all_devices={},
        send_command_fn=unexpected, get_file_link_fn=lambda *a: "", chat_history=[],
        load_llm_config_fn=lambda: {"model": "mock-model"}, pick_model_fn=lambda *a: "mock-model",
        chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
    assert calls == (["pipeline.plan", "pipeline.plan.retry"] if finish == "length" else ["pipeline.plan"])
    assert result["task_receipt"]["task_status"] == "failed"
    assert result["task_receipt"]["terminal_reason"] == "invalid_plan"
    assert not result["commands"] and not result["tasks"]


@pytest.mark.parametrize("choice", ["confirm", "deny", "cancel"])
def test_three_step_plan_survives_confirmation_with_same_task_and_context(monkeypatch, choice):
    tid, did = "three-step-plan", "1:pc"
    monkeypatch.setitem(runtime.tasks, tid, {
        "task_id": tid, "user_id": 1, "chat_id": 1, "message": REQUEST,
        "device_ids": [did], "status": "running", "results": {}, "commands": [],
        "modes": {"pipeline": True}, "created_at": time.time()})
    monkeypatch.setitem(runtime.devices, did, {
        "user_id": 1, "info": {"hostname": "pc", "os": "Windows"}, "pending": {}})
    dispatched, phases, stored, updates, finishes, messages = [], [], [], {}, [], []
    paths = ["C:/Users/demo/Desktop/IRU/report." + ext for ext in ("pptx", "docx", "xlsx")]
    plan = {"goal": "Сокращённая цель", "steps": [
        {"title": "Проверка среды", "instruction": "probe"},
        {"title": "Создание документов", "instruction": "create"},
        {"title": "Проверка результата", "instruction": "verify"},
    ]}
    def create_task(**kw):
        stored.append(kw)
        updates.update({i: "pending" for i in range(len(kw["steps"]))})
        return 101
    monkeypatch.setattr(pipeline.db, "create_task", create_task)
    monkeypatch.setattr(pipeline.db, "update_step", lambda task, idx, status, **kw: updates.__setitem__(idx, status))
    monkeypatch.setattr(pipeline.db, "finish_task", lambda task, status: finishes.append(status))
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [{"id": 101, "steps": [
        {"idx": i, "status": s} for i, s in updates.items()]}])
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(pipeline.db, "add_command_memory", lambda **kw: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    async def completion(**kwargs):
        phase = kwargs["phase"]
        phases.append(phase)
        if phase == "pipeline.plan":
            return {"choices": [{"message": {"content": json.dumps(plan)}}]}
        if phase == "pipeline.final":
            payload = json.loads(kwargs["messages"][-1]["content"])
            assert payload["original_request"] == REQUEST
            assert payload["pipeline_status"] == "completed"
            call = _answer_text_call("final", "Документы созданы и проверены.", ["step_5"])
        else:
            index = int(phase.split("step_")[1].split(".")[0]) - 1
            assert REQUEST in kwargs["messages"][0]["content"]
            if index == 2:
                assert all(path in kwargs["messages"][0]["content"] for path in paths)
            call = (_execute_call(phase, ("probe", "create", "verify")[index])
                    if phase.endswith(".1") else _answer_text_call(phase, "Шаг готов.", ["step_1"]))
        return {"choices": [{"message": {"tool_calls": [call]}}]}
    async def send(device, action, params, **kwargs):
        command = params["command"]
        if command == "probe" and not kwargs.get("skip_confirm"):
            raise RuntimeError("CONFIRM_REQUIRED")
        # Both the UI task and the whole pipeline must still be running here.
        assert runtime.tasks[tid]["status"] == "running"
        assert not finishes
        dispatched.append(command)
        result = {"returncode": 0, "stdout": "WRITE_OK=True" if command == "probe" else "OK: " + command}
        if command != "probe":
            result["files_verified" if command == "verify" else "created_files"] = paths
        return result
    async def process(**kwargs):
        return await pipeline.process_pipeline_subagents(
            user_message=REQUEST, device_id="pc", device_info={"os": "Windows"},
            all_devices={}, send_command_fn=kwargs["send_command_fn"], get_file_link_fn=lambda *a: "",
            chat_history=[], user_id=1, chat_id=1, poll_task_id=tid,
            load_llm_config_fn=lambda: {"model": "mock-model"}, pick_model_fn=lambda *a: "mock-model",
            chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules="")
    async def probe(**kwargs): pass
    monkeypatch.setattr(runtime, "send_command_to_agent", send)
    monkeypatch.setattr(runtime, "process_nl_command", process)
    monkeypatch.setattr(runtime, "_probe_python_toolchain_if_needed", probe)
    monkeypatch.setattr(runtime, "get_user_devices", lambda uid: {did: runtime.devices[did]})
    monkeypatch.setattr(runtime, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(runtime, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(runtime, "add_message", lambda *a, **kw: messages.append(a))
    monkeypatch.setattr(runtime, "add_training_record", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 1})
    async def scenario():
        execution = asyncio.create_task(runtime.run_nl_task(tid, 1, REQUEST, [did], 1))
        try:
            for _ in range(100):
                if runtime.tasks[tid]["status"] == "confirm": break
                await asyncio.sleep(0)
            assert runtime.tasks[tid]["status"] == "confirm"
            review = runtime.tasks[tid]["plan_review"]
            assert not stored and not dispatched
            await routes.api_review_plan(tid, routes.PlanReviewBody(revision=review["revision"], action="approve"), None)
            for _ in range(100):
                if runtime.tasks[tid]["status"] == "confirm": break
                await asyncio.sleep(0)
            assert "plan_review" not in runtime.tasks[tid]
            assert updates == {0: "running", 1: "pending", 2: "pending"}
            assert not finishes and not dispatched
            if choice == "confirm":
                await routes.api_confirm_task(tid, None)
                assert runtime.tasks[tid]["status"] == "running"
                assert not finishes
            elif choice == "deny":
                await routes.api_deny_task(tid, None)
            else:
                await routes.api_cancel_task(tid, None)
            await asyncio.wait_for(execution, 5)
        finally:
            if not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
    asyncio.run(scenario())
    assert len(stored) == 1 and len(stored[0]["steps"]) == 3
    assert phases.count("pipeline.plan") == 1
    assert dispatched == (["probe", "create", "verify"] if choice == "confirm" else [])
    assert finishes == (["completed"] if choice == "confirm" else ["cancelled"])
    assert len(updates) == 3 and set(updates.values()) == ({"done"} if choice == "confirm" else {"cancelled"})
    assert runtime.tasks[tid]["status"] == ("done" if choice == "confirm" else "cancelled")
    assert all("Выполнено." not in str(message) for message in messages)

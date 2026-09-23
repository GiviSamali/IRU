import asyncio
import copy
import json
import pytest
from server import controller_pipeline as pipeline
from server.pipeline_step_control import StepProgress, completion_matches, step_handoff
from test_controller_pipeline_budget import _shared_context, _answer_call, _execute_call


def msg(call):
    return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}


def worker(completion, send, step=None, cfg=None):
    return asyncio.run(pipeline.run_pipeline_worker(
        client=None, cfg=cfg or {"model": "mock-model"}, model="mock-model", shared=_shared_context(),
        overall_goal="Create deliverable", step=step or {"title": "Create", "instruction": "Create deliverable"},
        completed_steps=[], chat_history=[], send_command_fn=send, get_file_link_fn=lambda *a: "link",
        machine_guid=None, mem_user_id=None, poll_task_id=None, chat_completion_request_fn=completion,
        worker_tools=[]))


def test_plan_limits_and_single_step():
    assert pipeline.PIPELINE_MAX_STEPS == 8
    assert pipeline.PIPELINE_WORKER_MAX_ITERATIONS == 12
    for size in (1, 6, 8):
        plan = pipeline.normalize_pipeline_plan({"steps": ["Create folder"] * size}, "goal", "pc")
        assert len(plan["steps"]) == size
    with pytest.raises(ValueError, match="maximum of 8"):
        pipeline.normalize_pipeline_plan({"steps": ["step"] * 9}, "goal", "pc")


def test_worker_hard_limit_includes_repair():
    counts = {"llm": 0, "tools": 0}
    async def completion(**kwargs):
        counts["llm"] += 1
        return msg(_execute_call(str(counts["llm"]), "read " + str(counts["llm"])))
    async def send(*args):
        counts["tools"] += 1
        return {"returncode": 0, "stdout": str(counts["tools"])}
    result = worker(completion, send)
    assert counts == {"llm": 12, "tools": 11}
    assert result["status"] == "error"


def test_generic_ok_does_not_finish_step_but_final_evidence_does():
    counts = {"llm": 0, "tools": 0}
    async def completion(**kwargs):
        counts["llm"] += 1
        assert counts["llm"] <= 2
        return msg(_execute_call(str(counts["llm"]), "action"))
    async def send(*args):
        counts["tools"] += 1
        return {"returncode": 0, "stdout": "OK: python_found" if counts["tools"] == 1 else "OK: deliverable_verified"}
    result = worker(completion, send, {"title": "Create", "completion_check": {
        "tool": "execute_cmd", "stdout_contains": "OK: deliverable_verified"}})
    assert result["status"] == "ok" and counts == {"llm": 2, "tools": 2}


def test_no_check_requires_grounded_answer_after_intermediate_ok():
    calls = [msg(_execute_call("1", "prepare")), msg(_execute_call("2", "create")), msg(_answer_call("3", "done"))]
    sent = []
    async def completion(**kwargs): return calls.pop(0)
    async def send(device, action, args):
        sent.append(args["command"])
        return {"returncode": 0, "stdout": "OK: " + args["command"]}
    result = worker(completion, send)
    assert sent == ["prepare", "create"] and result["status"] == "ok"


@pytest.mark.parametrize("failure", [False, True])
def test_no_progress_and_repeated_failure_stop(failure):
    counts = {"llm": 0, "tools": 0}
    async def completion(**kwargs):
        counts["llm"] += 1
        return msg(_execute_call(str(counts["llm"]), "same command"))
    async def send(*args):
        counts["tools"] += 1
        return {"error": "missing file"} if failure else {"returncode": 0, "stdout": "unchanged"}
    result = worker(completion, send)
    assert result["status"] == "error" and counts["llm"] < 12
    assert result["terminal_reason"] == ("recovery_exhausted" if failure else "no_progress")
    if failure: assert counts["tools"] == 2


def test_progress_allows_one_successful_recovery():
    calls = [msg(_execute_call("1", "initial")), msg(_execute_call("2", "fix")), msg(_answer_call("3", "done"))]
    async def completion(**kwargs): return calls.pop(0)
    async def send(device, action, args):
        return {"error": "missing"} if args["command"] == "initial" else {"returncode": 0, "stdout": "new result"}
    assert worker(completion, send)["status"] == "ok"


def test_auditor_rejections_are_bounded():
    counts = {"audit": 0}
    async def completion(**kwargs):
        if "auditor" in kwargs.get("phase", ""):
            counts["audit"] += 1
            return {"choices": [{"message": {"content": '{"valid":false,"reason":"unsupported"}'}}]}
        return msg(_answer_call("a", "done"))
    async def send(*args): raise AssertionError("no device call expected")
    assert worker(completion, send, cfg={"model": "mock-model", "answer_auditor_enabled": True})["status"] == "error"
    assert counts["audit"] == 2


def test_failed_result_cannot_match_completion_check():
    step = {"completion_check": {"tool": "execute_cmd", "stdout_contains": "OK: deliverable_verified"}}
    assert not completion_matches(step, {"action": "execute_cmd", "result": {"returncode": 1, "stdout": "OK: deliverable_verified"}})


@pytest.mark.parametrize("names", [["Создай одну папку"], ["research", "pptx", "docx", "xlsx", "website", "verify"]])
def test_plan_no_refine_and_shared_article_material(monkeypatch, names):
    captures, requests = [], []
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: 1)
    monkeypatch.setattr(pipeline.db, "update_step", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, "finish_task", lambda *a: None)
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [])
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    async def completion(**kwargs):
        requests.append(kwargs["phase"])
        if kwargs["phase"] == "pipeline.plan":
            return {"choices": [{"message": {"content": json.dumps({"steps": [{"title": n, "instruction": n, "device_id": "pc"} for n in names]})}}]}
        assert kwargs["phase"] == "pipeline.final"
        return msg(_answer_call("final", "done"))
    async def run_worker(**kwargs):
        captures.append(copy.deepcopy(kwargs["completed_steps"]))
        n = kwargs["step"]["title"]
        return {"status": "ok", "answer": "Article facts: revenue 42" if len(captures) == 1 else n,
                "commands": [{"action": "write_content", "result": {"path": n + ".txt", "facts": {"revenue": 42} if len(captures) == 1 else {}}}]}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", run_worker)
    async def send(*a): raise AssertionError("no device expected")
    asyncio.run(pipeline.process_pipeline_subagents(
        user_message="Прочитай статью и создай документы", device_id="pc", device_info={"os": "Windows"},
        all_devices={}, send_command_fn=send, get_file_link_fn=lambda *a: "", chat_history=[],
        load_llm_config_fn=lambda: {"model": "mock-model"}, pick_model_fn=lambda *a: "mock-model",
        chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
    assert requests == ["pipeline.plan", "pipeline.final"]
    assert len(captures) == len(names)
    for history in captures[1:]:
        assert history[0]["handoff"]["status"] == "done"
        assert history[0]["handoff"]["artifacts"] == [names[0] + ".txt"]
        assert "42" in json.dumps(history[0]["handoff"])
        prompt = pipeline.pipeline_worker_prompt(_shared_context("pc"), "goal", {"title": "next"}, history)
        assert "Article facts: revenue 42" in prompt
        assert names[0] + ".txt" in prompt


def test_handoff_is_bounded_and_does_not_include_command_history():
    handoff = step_handoff("facts" * 2000, [{"action": "execute_cmd", "command": "PRIVATE", "result": {"stdout": "x" * 10000}}] * 20, "done")
    assert len(handoff["summary"]) == 4000
    assert len(handoff["evidence"]) == 4
    assert "PRIVATE" not in json.dumps(handoff)


@pytest.mark.parametrize("choice", ["confirm", "deny", "cancel"])
def test_pipeline_confirmation_resumes_same_task_or_cancels(monkeypatch, choice):
    import time
    from server import task_runtime as rt
    from server.routers import tasks as routes
    from fastapi import HTTPException
    tid, did = "plan-confirm", "1:pc"
    rt.tasks[tid] = {"task_id": tid, "user_id": 1, "chat_id": 1, "message": "create",
        "device_ids": [did], "status": "running", "results": {}, "commands": [],
        "modes": {"pipeline": True, "autonomous": True}, "created_at": time.time()}
    rt.devices[did] = {"user_id": 1, "info": {"hostname": "pc", "os": "Windows"}, "pending": {}}
    dispatched = []
    async def send(device, action, params, **kwargs):
        if params["command"] == "needs approval" and not kwargs.get("skip_confirm"):
            raise RuntimeError("CONFIRM_REQUIRED")
        dispatched.append(params["command"])
        return {"returncode": 0, "stdout": "OK: result"}
    async def process(**kwargs):
        await kwargs["send_command_fn"]("pc", "execute_cmd", {"command": "needs approval"})
        await kwargs["send_command_fn"]("pc", "execute_cmd", {"command": "next step"})
        return {"answer": "done", "commands": [], "tasks": [], "task_receipt": {"task_status": "completed"}}
    async def probe(**kwargs): pass
    monkeypatch.setattr(rt, "send_command_to_agent", send)
    monkeypatch.setattr(rt, "process_nl_command", process)
    monkeypatch.setattr(rt, "_probe_python_toolchain_if_needed", probe)
    monkeypatch.setattr(rt, "get_user_devices", lambda uid: {did: rt.devices[did]})
    monkeypatch.setattr(rt, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(rt, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "add_training_record", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "enforce_trusted_answer", lambda answer, commands: answer)
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 1})
    async def scenario():
        execution = asyncio.create_task(rt.run_nl_task(tid, 1, "create", [did], 1))
        for _ in range(50):
            if rt.tasks[tid]["status"] == "confirm": break
            await asyncio.sleep(0)
        assert rt.tasks[tid]["status"] == "confirm" and not dispatched
        if choice == "confirm":
            await routes.api_confirm_task(tid, None)
            with pytest.raises(HTTPException):
                await routes.api_confirm_task(tid, None)
        elif choice == "deny":
            await routes.api_deny_task(tid, None)
        else:
            await routes.api_cancel_task(tid, None)
        await asyncio.wait_for(execution, 1)
        assert "_pipeline_confirm_future" not in rt.tasks[tid]
    asyncio.run(scenario())
    assert dispatched == (["needs approval", "next step"] if choice == "confirm" else [])
    assert rt.tasks[tid]["status"] == ("done" if choice == "confirm" else "cancelled")

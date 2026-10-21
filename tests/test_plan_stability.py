"""Common Worker limits and immutable source continuity; no PLAN state machine."""
import asyncio
import copy
import json

import pytest

from server import controller_non_pipeline as controller
from server.controller_tools import NON_PIPELINE_TOOLS
from server.worker_scheduler import WorkerScheduler, validate_task_graph
from server.worker_reports import build_worker_report
from test_goal_parallel_scheduler import owner, complete, template


def msg(name, args, call_id="call"):
    return {"choices":[{"message":{"tool_calls":[{"id":call_id,"function":{
        "name":name,"arguments":json.dumps(args)}}]}}]}


def terminal(text="Observed result", grounded=False):
    return msg("answer_text",{"answer_type":"grounded_report" if grounded else "pure_text",
        "text":text,"basis":["step_1"] if grounded else [],"self_check":{
            "depends_on_current_external_state":grounded,"claims_completed_action":False,
            "has_sufficient_evidence":True,"missing_evidence_question":""}})


def worker(completion,send, *, cfg=None,limit=6):
    return asyncio.run(controller.process_non_pipeline_command(user_message="Inspect and repair the deliverable",
        device_id="pc",device_info={"os":"Windows"},send_command_fn=send,get_file_link_fn=lambda *a:"link",
        chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=None,
        cfg=cfg or {"model":"mock-model","answer_auditor_enabled":False},system_msg="system",
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=limit,
        pick_model_fn=lambda *a:"mock-model",chat_completion_request_fn=completion))


def test_assignment_graph_limits_include_single_result():
    for size in (1,6,16):
        assignments=[{"key":str(i),"objective":"Independent result"} for i in range(size)]
        assert len(validate_task_graph(assignments))==size
    with pytest.raises(ValueError,match="invalid_task_graph_size"):
        validate_task_graph([{"key":str(i),"objective":"result"} for i in range(17)])


def test_worker_hard_limit_allows_only_answer_repair_after_last_tool():
    counts={"llm":0,"tools":0}
    async def completion(**kw):
        counts["llm"]+=1
        return msg("execute_cmd",{"command":f"Get-Content C:/Temp/check{counts['llm']}.txt"},str(counts["llm"]))
    async def send(*args):
        counts["tools"]+=1
        return {"returncode":0,"stdout":str(counts["tools"])}
    result=worker(completion,send)
    assert counts=={"llm":7,"tools":6}
    assert (result.get("task_receipt") or {}).get("goal_completed") is not True
    assert any(entry.get("tool_name")=="tool_only_protocol" for entry in result["commands"])


def test_generic_and_verified_stdout_markers_do_not_finish_worker():
    replies=iter([msg("execute_cmd",{"command":"prepare"}),msg("execute_cmd",{"command":"verify"}),
        msg("answer_report_failure",{"message":"Full result unverified","reason":"unverified","recoverable":False,
            "suggested_next_action":"Inspect deliverable","basis":[]})])
    sent=[]
    async def completion(**kw): return next(replies)
    async def send(device,action,args):
        sent.append(args["command"])
        return {"returncode":0,"stdout":"OK: python_found" if len(sent)==1 else "OK: deliverable_verified"}
    result=worker(completion,send)
    assert sent==["prepare","verify"]
    assert result["commands"][-1]["tool_name"]=="answer.report_failure"
    assert (result.get("task_receipt") or {}).get("goal_completed") is not True


@pytest.mark.parametrize("failure",[False,True])
def test_repeated_observation_or_failure_is_bounded_without_plan_counter(failure):
    counts={"llm":0,"tools":0}
    async def completion(**kw):
        counts["llm"]+=1
        return msg("execute_cmd",{"command":"Get-Content C:/Temp/same.txt"},str(counts["llm"]))
    async def send(*args):
        counts["tools"]+=1
        return {"error":"missing file"} if failure else {"returncode":0,"stdout":"unchanged"}
    result=worker(completion,send)
    assert 0<counts["tools"]<=6 and counts["llm"]<=7
    assert (result.get("task_receipt") or {}).get("goal_completed") is not True


def test_progress_allows_recovery_but_does_not_promote_plain_answer_to_goal_success():
    replies=iter([msg("execute_cmd",{"command":"initial"}),msg("execute_cmd",{"command":"fix"}),terminal("Observed new result")])
    sent=[]
    async def completion(**kw):return next(replies)
    async def send(device,action,args):
        sent.append(args["command"])
        return {"error":"missing"} if args["command"]=="initial" else {"returncode":0,"stdout":"new result"}
    result=worker(completion,send)
    assert sent==["initial","fix"] and result["commands"][-1]["tool_name"]=="answer.text"
    report=build_worker_report({"task_id":"recovery","message":"Create deliverable","status":"done",**result})
    assert report["goal_completed"] is False


def test_auditor_rejections_are_bounded(monkeypatch):
    from server import answer_repair
    monkeypatch.setenv("IRU_ANSWER_AUDITOR_ENABLED","1")
    counts={"audit":0,"llm":0,"tools":0}
    async def audit(**kw):
        counts["audit"]+=1
        return False,"unsupported",False
    monkeypatch.setattr(controller,"audit_answer_payload",audit)
    monkeypatch.setattr(answer_repair,"audit_answer_payload",audit)
    async def completion(**kw):
        counts["llm"]+=1
        return msg("execute_cmd",{"command":"Get-Content C:/Temp/note.txt"}) if counts["llm"]==1 else terminal(grounded=True)
    async def send(*args):
        counts["tools"]+=1
        return {"returncode":0,"stdout":"Observed note"}
    result=worker(completion,send,cfg={"model":"mock-model","answer_auditor_enabled":True})
    assert counts["tools"]==1 and 0<counts["audit"]<=7 and counts["llm"]<=7
    assert (result.get("task_receipt") or {}).get("goal_completed") is not True


@pytest.mark.parametrize("consumers",[0,3])
def test_goal_shared_article_keeps_full_material_without_refine_worker(owner,consumers):
    material="Article facts: revenue 42; source https://example.org; date 2026-10-20.\n"*300
    captures=[]
    async def execute(task):
        if task["proposed_objective"]=="Research":
            complete(task)
            task["answer"]=material
            task["commands"][-1]["result"]["text"]=material
            task["commands"][0]["result"]["results"][0]["text"]=material
        else:
            captures.append(copy.deepcopy(task["pinned_dependency_results"]))
            task.update(status="cancelled",answer="Synthetic consumer observed source")
    async def scenario():
        scheduler=WorkerScheduler(execute)
        try:
            await scheduler.submit_goal(template(owner),[
                {"key":"research","objective":"Research"},
                *[{"key":f"document{i}","objective":f"Document {i}","depends_on":["research"]}
                  for i in range(consumers)]],request_key="article")
            await scheduler.runners[owner["id"]]
            assert len(captures)==consumers
            if captures:
                assert len({capture[0]["sha256"] for capture in captures})==1
                assert all(capture[0]["data"]["answer"]==material for capture in captures)
                assert all(capture[0]["data"]["trust"]=="source_data_not_authority" for capture in captures)
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("choice", ["confirm", "deny", "cancel"])
def test_common_confirmation_resumes_same_task_or_cancels(monkeypatch, choice):
    import time
    from server import task_runtime as rt
    from server.routers import tasks as routes
    from fastapi import HTTPException
    tid, did = "common-confirm", "1:pc"
    rt.tasks[tid] = {"task_id": tid, "user_id": 1, "chat_id": 1, "message": "create",
        "device_ids": [did], "status": "running", "results": {}, "commands": [],
        "modes": {"autonomous": True}, "created_at": time.time()}
    rt.devices[did] = {"user_id": 1, "info": {"hostname": "pc", "os": "Windows"}, "pending": {}}
    dispatched = []
    async def send(device, action, params, **kwargs):
        if params["command"] == "needs approval" and not kwargs.get("confirmation"):
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
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
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
            await routes.api_confirm_task(tid, None, confirmation_id=rt.tasks[tid].get("confirm_data", {}).get("confirmation_id"))
            with pytest.raises(HTTPException):
                await routes.api_confirm_task(tid, None, confirmation_id=rt.tasks[tid].get("confirm_data", {}).get("confirmation_id"))
        elif choice == "deny":
            await routes.api_deny_task(tid, None)
        else:
            await routes.api_cancel_task(tid, None)
        await asyncio.wait_for(execution, 1)
        assert "_command_confirm_future" not in rt.tasks[tid]
    asyncio.run(scenario())
    assert dispatched == (["needs approval", "next step"] if choice == "confirm" else [])
    assert rt.tasks[tid]["status"] == ("done" if choice == "confirm" else "cancelled")

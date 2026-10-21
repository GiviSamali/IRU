"""Unified Worker budget and device-context regressions; synthetic effects only."""
import asyncio
import json

import pytest

from server import controller, database as db
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.worker_context import build_worker_context


def _make_completion_fn(responses, captured_messages=None):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        if captured_messages is not None:
            captured_messages.append(kwargs.get("messages"))
        assert queue, "No more mocked LLM responses left"
        response = queue.pop(0)
        if kwargs.get("tools") is not None:
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            if len(tool_calls) > 1:
                queue[:0] = [
                    {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [call]}}]}
                    for call in tool_calls[1:]
                ]
                return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [tool_calls[0]]}}]}
            if not tool_calls:
                return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_call("call-answer", message.get("content") or "ok")]}}]}
        return response

    return _chat_completion_request_fn


def _answer_call(call_id: str, text: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "answer_text",
            "arguments": json.dumps({
                "answer_type": "pure_text",
                "text": text,
                "basis": [],
                "self_check": {
                    "depends_on_current_external_state": False,
                    "claims_completed_action": False,
                    "has_sufficient_evidence": True,
                    "missing_evidence_question": "",
                },
            }, ensure_ascii=False),
        },
    }


def _execute_call(call_id, command, device_id=None):
    args = {"command": command}
    if device_id:
        args["device_id"] = device_id
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps(args),
        },
    }


def _run_worker_case(responses, send_command_fn=None):
    async def send(device,action,params):
        if device!="device-1":
            raise RuntimeError("device_outside_worker_assignment")
        if send_command_fn:return await send_command_fn(device,action,params)
        return {"returncode":0,"stdout":"ok","stderr":""}
    return asyncio.run(process_non_pipeline_command(user_message="Inspect the selected device",
        device_id="device-1",device_info={"os":"Windows","hostname":"devbox"},send_command_fn=send,
        get_file_link_fn=lambda *a:"/api/download/mock",chat_history=[],user_id=None,chat_id=None,
        modes={},poll_task_id=None,cfg={"model":"mock-model","answer_auditor_enabled":False},system_msg="system",
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=6,
        pick_model_fn=lambda *a:"mock-model",chat_completion_request_fn=_make_completion_fn(responses)))


def responses(calls):
    return [{"choices":[{"message":{"tool_calls":calls}}]},
        {"choices":[{"message":{"tool_calls":[_answer_call("answer","Observed result")]}}]}]


def test_common_worker_stops_many_commands_at_its_iteration_boundary():
    executed=[]
    async def send(device,action,params):
        executed.append(params["command"])
        return {"returncode":0,"stdout":"ok","stderr":""}
    result=_run_worker_case(responses([_execute_call(str(i),f"whoami {i}") for i in range(21)]),send)
    assert 0<len(executed)<=6
    assert (result.get("task_receipt") or {}).get("goal_completed") is not True


def test_common_worker_does_not_budget_block_similar_explicit_commands():
    commands=["Start-Process calc.exe",'Start-Process -FilePath "calc.exe"',"Start-Process calc","Start-Process -FilePath calc.exe"]
    executed=[]
    async def send(device,action,params):
        executed.append(params["command"])
        return {"returncode":0,"stdout":"ok","stderr":""}
    result=_run_worker_case(responses([_execute_call(str(i),command) for i,command in enumerate(commands)]),send)
    assert executed==commands
    assert "budget_guard" not in [entry.get("action") for entry in result["commands"]]


def test_common_worker_single_command_is_not_blocked():
    executed=[]
    async def send(device,action,params):
        executed.append((device,action,params["command"]))
        return {"returncode":0,"stdout":"user","stderr":""}
    result=_run_worker_case(responses([_execute_call("who","whoami")]),send)
    assert executed==[("device-1","execute_cmd","whoami")]
    assert result["answer"]=="Observed result"


def test_device_override_is_denied_instead_of_silently_retargeted():
    seen=[]
    async def send(device,action,params):
        seen.append(device)
        return {"returncode":0,"stdout":"wrong"}
    result=_run_worker_case(responses([_execute_call("other","whoami","device-2")]),send)
    assert seen==[]
    assert result["commands"][0]["result"]["error"]=="device_outside_worker_assignment"


@pytest.fixture
def context_owner(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"context.sqlite")
    db.init_db()
    owner=db.create_user("context-owner")
    owner["chat_id"]=db.create_chat(owner["id"],"context")["id"]
    return owner


def device_a_context(monkeypatch,owner):
    profile={"device_id":"device-a","hostname":"alpha","os":"Windows","os_version":"11",
             "desktop_path":r"C:\Users\alpha\Desktop","machine_guid":"guid-a"}
    devices={"device-a":{"info":profile},"device-b":{"info":{"hostname":"bravo","os":"Windows",
        "desktop_path":r"D:\bravo\Desktop","project_root":r"D:\bravo\secret-project",
        "machine_guid":"guid-b","output":"secret-output"}}}
    monkeypatch.setattr(controller,"load_llm_config",lambda:{"model":"mock-model"})
    def memory(guid,user,device):
        assert guid=="guid-a" and device=="device-a"
        return "target memory only"
    monkeypatch.setattr(controller,"build_memory_block",memory)
    runtime=controller._build_runtime_context(device_id="device-a",all_devices=devices,
        device_info=profile,device_profile=profile,user_id=owner["id"],chat_history=[])
    return runtime,controller._build_non_pipeline_system_prompt(runtime=runtime,device_id="device-a")


def test_common_worker_context_excludes_other_device_paths_profile_and_memory(context_owner,monkeypatch):
    runtime,prompt=device_a_context(monkeypatch,context_owner)
    assert runtime.machine_guid=="guid-a"
    assert all(value in prompt for value in ("device-a","alpha","device-b","bravo","target memory only"))
    assert all(value not in prompt for value in (r"D:\bravo\Desktop",r"D:\bravo\secret-project","guid-b","secret-output"))


def test_common_worker_messages_exclude_foreign_worker_paths(context_owner,monkeypatch):
    runtime,prompt=device_a_context(monkeypatch,context_owner)
    history=build_worker_context(context_owner["id"],context_owner["chat_id"],"Inspect A",
        [f"{context_owner['id']}:device-a"],[{"role":"assistant","taskKind":"worker",
          "assignmentDeviceIds":["device-b"],"_taskId":"foreign-device-job","content":r"use D:\bravo\secret.txt"}])
    captured=[]
    async def completion(**kw):
        captured.append(kw["messages"])
        return {"choices":[{"message":{"tool_calls":[_answer_call("done","Context inspected")]}}]}
    async def send(*args):pytest.fail("No device command expected")
    asyncio.run(process_non_pipeline_command(user_message="Inspect A",device_id="device-a",device_info={"os":"Windows"},
        send_command_fn=send,get_file_link_fn=lambda *a:"",chat_history=history,user_id=None,chat_id=None,modes={},
        poll_task_id=None,cfg={"model":"mock-model"},system_msg=prompt,machine_guid=None,mem_user_id=None,
        non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=3,pick_model_fn=lambda *a:"mock-model",
        chat_completion_request_fn=completion))
    combined=json.dumps(captured,ensure_ascii=False)
    for forbidden in ("secret.txt","secret-project","guid-b","secret-output"):
        assert forbidden not in combined

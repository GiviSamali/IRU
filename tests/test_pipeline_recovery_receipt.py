"""Common Worker recovery and evidence. No model, shell or agent is run by import."""
import asyncio
import json

import pytest

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.controller_shared import build_recent_artifact_context, format_recent_artifact_context_block
from server.worker_reports import build_worker_report
from server.tool_completion import tool_result_terminal_sufficient


def _execute_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def _answer_text_call(call_id: str, text: str, basis: list[str]) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "answer_text",
            "arguments": json.dumps({
                "answer_type": "grounded_report",
                "text": text,
                "basis": basis,
                "self_check": {
                    "depends_on_current_external_state": True,
                    "claims_completed_action": True,
                    "has_sufficient_evidence": True,
                    "missing_evidence_question": "",
                },
            }),
        },
    }


def _answer_failure_call(call_id: str, message: str, basis: list[str]) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "answer_report_failure",
            "arguments": json.dumps({
                "message": message,
                "reason": "step command failed",
                "recoverable": True,
                "suggested_next_action": "continue with verification",
                "basis": basis,
            }),
        },
    }


def _completion_fn(responses, captured_messages):
    async def _chat_completion_request_fn(**kwargs):
        captured_messages.append(kwargs["messages"])
        assert responses, "Unexpected LLM call"
        return responses.pop(0)

    return _chat_completion_request_fn



def message(call):
    return {"choices":[{"message":{"tool_calls":[call]}}]}


def run_worker(responses,send, *, device_tool_fn=None,history=None):
    captured=[]
    outcome=asyncio.run(process_non_pipeline_command(user_message="Inspect the environment and prepare the requested files",
        device_id="device-a",device_info={"os":"Windows","hostname":"alpha"},send_command_fn=send,
        get_file_link_fn=lambda *a:"/api/download/mock",chat_history=history or [],user_id=None,chat_id=None,
        modes={},poll_task_id=None,cfg={"model":"mock-model","answer_auditor_enabled":False},system_msg="system",
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=6,
        pick_model_fn=lambda *a:"mock-model",chat_completion_request_fn=_completion_fn(responses,captured),
        device_tool_fn=device_tool_fn))
    return outcome,captured


def test_common_worker_keeps_dependency_error_and_later_verification_without_false_completion():
    responses=[message(_execute_call("import",'python -c "import docx"')),
        message(_execute_call("verify","verify generated docx files")),
        message(_answer_failure_call("final","The complete deliverable is unverified",["step_1","step_2"]))]
    sent=[]
    async def send(device,action,params):
        sent.append(params["command"])
        if len(sent)==1:
            return {"returncode":1,"stdout":"","stderr":"ModuleNotFoundError: No module named 'docx'"}
        return {"returncode":0,"stdout":"IRU_VERIFIED=1 7 files","stderr":"",
            "files_verified":["C:/Temp/file1.docx"],"artifacts_created":["C:/Temp/file1.docx"]}
    outcome,_=run_worker(responses,send)
    assert len(sent)==2
    assert outcome["commands"][0]["result"]["error_type"]=="dependency_missing"
    assert outcome["commands"][1]["result"]["files_verified"]==["C:/Temp/file1.docx"]
    assert outcome["commands"][-1]["tool_name"]=="answer.report_failure"
    report=build_worker_report({"task_id":"recovery","message":"Create documents","status":"done",**outcome})
    assert report["goal_completed"] is False


def test_common_worker_dispatches_advertised_runtime_tool_through_device_api():
    calls=[]
    responses=[message({"id":"runtime","function":{"name":"device_check_runtime","arguments":"{}"}}),
        message(_answer_failure_call("final","No document has been created",["step_1"]))]
    async def send(*args):
        pytest.fail("Runtime tool must not use raw command transport")
    async def device_tool(name,args):
        calls.append((name,args["device_id"]))
        return {"status":"ok","device_id":args["device_id"],"runtime_summary":{
            "runtime_status":"ok","python_version":"3.11.9","pip_status":"ok"}}
    outcome,_=run_worker(responses,send,device_tool_fn=device_tool)
    assert calls==[("device_check_runtime","device-a")]
    entry=outcome["commands"][0]
    assert entry["tool_name"]=="device.check_runtime" and entry["tool_type"]=="typed"
    assert entry["result"]["runtime_summary"]["runtime_status"]=="ok"


def test_recent_file_context_preserves_exact_paths_without_promoting_past_evidence():
    path="C:/Users/demo/Desktop/CourseLaunchKit/description.docx"
    history=[{"id":7,"role":"assistant","commands":[{"tool_name":"write_content","step_id":"old_step",
        "status":"success","result":{"path":path,"bytes_written":12,"content_sha256":"a"*64}}]}]
    context=build_recent_artifact_context(history)
    assert [item["path"] for item in context["created_files"]]==[path]
    block=format_recent_artifact_context_block(context)
    assert path in block and "not current-run evidence" in block
    sent=[]
    async def send(device,action,params):
        sent.append(params["command"])
        return {"returncode":0,"stdout":"opened","stderr":""}
    outcome,_=run_worker([message(_execute_call("exact",f'Start-Process "{path}"')),
        message(_answer_failure_call("final","Opened state is unverified",["step_1"]))],send,history=history)
    assert sent==[f'Start-Process "{path}"']
    assert outcome["commands"][-1]["tool_name"]=="answer.report_failure"


@pytest.mark.parametrize("stdout",["NOT OK","IRU_VERIFIED=1","OK: verified"])
def test_printed_markers_are_not_whole_goal_verification(stdout):
    entry={"tool_name":"execute_cmd","step_id":"step_1","status":"success",
        "result":{"returncode":0,"stdout":stdout,"stderr":""}}
    assert not tool_result_terminal_sufficient(entry)
    report=build_worker_report({"task_id":"printed-marker","message":"Create and verify documents",
        "status":"done","commands":[entry],"task_receipt":{"task_status":"completed",
            "final_verification_status":"verified","goal_completed":True}})
    assert not report["goal_completed"]

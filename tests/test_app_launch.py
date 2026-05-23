import asyncio
import json
import sys
from pathlib import Path

from server.controller_non_pipeline import process_non_pipeline_command


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _tool_call(call_id: str, name: str, args: dict | None = None) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}),
        },
    }


def test_app_launch_returns_pid_and_verified_window(monkeypatch):
    from core import actions

    class Proc:
        pid = 4321

    monkeypatch.setattr(actions.os, "name", "nt")
    monkeypatch.setattr(actions.subprocess, "Popen", lambda *args, **kwargs: Proc())
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)
    monkeypatch.setattr(actions, "window_find", lambda **kwargs: {
        "status": "found",
        "match": {"pid": 4321, "title": "Demo", "visible": True, "process_name": "python.exe"},
    })

    result = actions.app_launch("python app.py", expected_title="Demo", timeout_sec=0)

    assert result["status"] == "launched_verified"
    assert result["pid"] == 4321
    assert result["window"]["title"] == "Demo"


def test_app_verify_launch_reports_process_alive_no_window(monkeypatch):
    from core import actions

    monkeypatch.setattr(actions.os, "name", "nt")
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: [])
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)

    result = actions.app_verify_launch(pid=4321, timeout_sec=0)

    assert result["status"] == "process_alive_no_window"
    assert result["verified"] is False
    assert result["process_alive"] is True


def test_non_pipeline_app_launch_rewrites_python_to_managed_runtime():
    sent = []
    venv_python = r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe"

    async def chat_completion_request_fn(**kwargs):
        tool_messages = [msg for msg in kwargs["messages"] if msg.get("role") == "tool"]
        if not tool_messages:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call("call-runtime", "device_prepare_runtime", {})]},
                }]
            }
        if len(tool_messages) == 1:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call("call-launch", "app_launch", {"command": "python app.py", "expected_title": "Demo"})]},
                }]
            }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }]
        }

    async def device_tool_fn(name, args):
        return {
            "status": "ok",
            "device_id": args["device_id"],
            "runtime_summary": {
                "runtime_status": "ok",
                "venv_python": venv_python,
                "python_version": "3.11.9",
                "pip_status": "ok",
            },
        }

    async def send_command_fn(device_id, action, params):
        sent.append((device_id, action, params))
        return {"status": "launched", "pid": 4321, "process_alive": True, "window": None, "next_actions": ["window.verify"]}

    result = asyncio.run(process_non_pipeline_command(
        user_message="launch app",
        device_id="givi",
        device_info={"hostname": "GIVI", "os": "Windows"},
        send_command_fn=send_command_fn,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=None,
        chat_id=None,
        modes={},
        poll_task_id=None,
        cfg={"model": "mock", "max_tokens": 512},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=4,
        pick_model_fn=lambda cfg, modes: "mock",
        chat_completion_request_fn=chat_completion_request_fn,
        device_tool_fn=device_tool_fn,
    ))

    assert sent == [("givi", "app.launch", {"command": f'& "{venv_python}" app.py', "expected_title": "Demo"})]
    assert result["commands"][1]["tool_name"] == "app.launch"
    assert result["commands"][1]["tool_type"] == "typed"


def test_non_pipeline_window_verify_logs_tool():
    sent = []

    async def chat_completion_request_fn(**kwargs):
        if not any(msg.get("role") == "tool" for msg in kwargs["messages"]):
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call("call-window", "window_verify", {"pid": 4321})]},
                }]
            }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }]
        }

    async def send_command_fn(device_id, action, params):
        sent.append((device_id, action, params))
        return {"status": "verified", "verified": True, "pid": 4321, "window": {"title": "Demo", "visible": True}}

    result = asyncio.run(process_non_pipeline_command(
        user_message="verify window",
        device_id="givi",
        device_info={"hostname": "GIVI", "os": "Windows"},
        send_command_fn=send_command_fn,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=None,
        chat_id=None,
        modes={},
        poll_task_id=None,
        cfg={"model": "mock", "max_tokens": 512},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=3,
        pick_model_fn=lambda cfg, modes: "mock",
        chat_completion_request_fn=chat_completion_request_fn,
    ))

    assert sent == [("givi", "window.verify", {"pid": 4321})]
    assert result["commands"][0]["tool_name"] == "window.verify"
    assert result["commands"][0]["summary"].startswith("status=verified")

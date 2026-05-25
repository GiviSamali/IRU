import asyncio
import json

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE
from server.controller_tools import WORKER_TOOLS
from server.tool_registry import compact_device_passport, list_tools, tool_log_entry


def _tool_call(name: str, args: dict | None = None) -> dict:
    return {
        "id": f"call-{name}",
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}),
        },
    }


def _tool_call_with_id(call_id: str, name: str, args: dict | None = None) -> dict:
    call = _tool_call(name, args)
    call["id"] = call_id
    return call


async def _run_non_pipeline(tool_name: str, args: dict | None = None, *, send_command_fn=None, device_tool_fn=None):
    async def chat_completion_request_fn(**kwargs):
        messages = kwargs["messages"]
        if not any(msg.get("role") == "tool" for msg in messages):
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call(tool_name, args)]},
                }]
            }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }]
        }

    async def default_send(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": "", "path": params.get("path")}

    return await process_non_pipeline_command(
        user_message="run",
        device_id="givi",
        device_info={"hostname": "GIVI", "os": "Windows"},
        send_command_fn=send_command_fn or default_send,
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
        device_tool_fn=device_tool_fn,
    )


def test_system_list_tools_returns_compact_grouped_tools():
    registry = list_tools("all")

    assert "device" in registry
    assert "python" in registry
    assert "window" in registry
    assert "app" in registry
    assert "fallback" in registry
    assert any(tool["name"] == "device.refresh_state" for tool in registry["device"])
    assert any(tool["name"] == "device.prepare_runtime" for tool in registry["python"])
    assert any(tool["name"] == "window.verify" for tool in registry["window"])
    assert any(tool["name"] == "app.launch" for tool in registry["app"])
    execute = next(tool for tool in registry["fallback"] if tool["name"] == "execute_cmd")
    assert execute["purpose"].lower().startswith("low-level shell fallback")
    assert "stdout" not in json.dumps(registry).lower()


def test_device_get_passport_compact_and_has_context_handles():
    passport = compact_device_passport(
        "givi",
        {
            "info": {"hostname": "GIVI"},
            "ws": object(),
            "activation_receipt": {"secret": "full receipt should not leak"},
            "activation_summary": {"activation_status": "activated", "runtime_status": "ok"},
            "recent_traces": [{"raw": "trace"}],
        },
        None,
    )

    assert passport["device_id"] == "givi"
    assert passport["hostname"] == "GIVI"
    assert passport["context_handles"]["device_state"] == "ctx://device/givi/state"
    dumped = json.dumps(passport, ensure_ascii=False)
    assert "full receipt should not leak" not in dumped
    assert '"raw": "trace"' not in dumped


def test_device_refresh_state_tool_log_entry_is_compact():
    entry = tool_log_entry(
        "device_refresh_state",
        {
            "status": "ok",
            "device_id": "givi",
            "health_summary": {"health_status": "warning"},
            "raw_stdout": "x" * 500,
        },
        target_device_id="givi",
    )

    assert entry["tool_name"] == "device.refresh_state"
    assert entry["tool_type"] == "typed"
    assert entry["summary"] == "status=ok; health=warning"
    assert "x" * 100 not in entry["summary"]


def test_device_runtime_tool_log_entry_is_compact():
    entry = tool_log_entry(
        "device_prepare_runtime",
        {
            "status": "ok",
            "runtime_summary": {"runtime_status": "ok", "python_version": "3.11.9"},
            "raw_stdout": "x" * 500,
        },
        target_device_id="givi",
    )

    assert entry["tool_name"] == "device.prepare_runtime"
    assert entry["tool_type"] == "typed"
    assert entry["summary"] == "runtime=ok"
    assert "x" * 100 not in entry["summary"]


def test_window_and_app_tool_log_entries_are_compact():
    launch = tool_log_entry(
        "app_launch",
        {
            "status": "launched_verified",
            "pid": 4321,
            "window": {"title": "Demo", "process_name": "python.exe", "visible": True},
            "raw_stdout": "x" * 500,
        },
        target_device_id="givi",
    )
    verify = tool_log_entry(
        "window_verify",
        {
            "status": "verified",
            "verified": True,
            "window": {"pid": 4321, "title": "Demo", "process_name": "python.exe"},
            "raw_stdout": "x" * 500,
        },
        target_device_id="givi",
    )

    assert launch["tool_name"] == "app.launch"
    assert launch["tool_type"] == "typed"
    assert "status=launched_verified" in launch["summary"]
    assert "x" * 100 not in launch["summary"]
    assert verify["tool_name"] == "window.verify"
    assert "status=verified" in verify["summary"]


def test_device_refresh_state_tool_uses_callback_and_logs_tool():
    calls = []

    async def device_tool_fn(name, args):
        calls.append((name, args["device_id"]))
        return {
            "status": "ok",
            "device_id": args["device_id"],
            "health_summary": {"health_status": "ok"},
            "state_handle": f"ctx://device/{args['device_id']}/state",
        }

    result = asyncio.run(_run_non_pipeline("device_refresh_state", {}, device_tool_fn=device_tool_fn))

    assert calls == [("device_refresh_state", "givi")]
    entry = result["commands"][0]
    assert entry["tool_name"] == "device.refresh_state"
    assert entry["tool_status"] == "success"
    assert entry["summary"] == "status=ok; health=ok"


def test_activation_tools_call_expected_modes_and_log_compact_summary():
    calls = []

    async def device_tool_fn(name, args):
        calls.append(name)
        return {
            "status": "ok",
            "device_id": args["device_id"],
            "activation_summary": {"activation_status": "activated", "runtime_status": "ok", "receipt_hash": "abc"},
        }

    soft = asyncio.run(_run_non_pipeline("device_activate", {}, device_tool_fn=device_tool_fn))
    repair = asyncio.run(_run_non_pipeline("device_repair_activation", {}, device_tool_fn=device_tool_fn))

    assert calls == ["device_activate", "device_repair_activation"]
    assert soft["commands"][0]["tool_name"] == "device.activate"
    assert repair["commands"][0]["tool_name"] == "device.repair_activation"
    assert "activation=" in soft["commands"][0]["summary"]


def test_runtime_tools_call_callback_and_log_compact_summary():
    calls = []

    async def device_tool_fn(name, args):
        calls.append((name, args["device_id"]))
        return {
            "status": "ok",
            "device_id": args["device_id"],
            "runtime_summary": {"runtime_status": "ok", "python_version": "3.11.9", "pip_status": "ok"},
        }

    check = asyncio.run(_run_non_pipeline("device_check_runtime", {}, device_tool_fn=device_tool_fn))
    prepare = asyncio.run(_run_non_pipeline("device_prepare_runtime", {}, device_tool_fn=device_tool_fn))
    repair = asyncio.run(_run_non_pipeline("device_repair_runtime", {}, device_tool_fn=device_tool_fn))

    assert calls == [("device_check_runtime", "givi"), ("device_prepare_runtime", "givi"), ("device_repair_runtime", "givi")]
    assert check["commands"][0]["tool_name"] == "device.check_runtime"
    assert prepare["commands"][0]["tool_name"] == "device.prepare_runtime"
    assert repair["commands"][0]["tool_name"] == "device.repair_runtime"
    assert prepare["commands"][0]["summary"] == "runtime=ok"


def test_non_pipeline_uses_freshly_prepared_runtime_for_following_execute_cmd():
    sent = []
    venv_python = r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe"

    async def chat_completion_request_fn(**kwargs):
        tool_messages = [msg for msg in kwargs["messages"] if msg.get("role") == "tool"]
        if not tool_messages:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call_with_id("call-runtime", "device_prepare_runtime", {})]},
                }]
            }
        if len(tool_messages) == 1:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_tool_call_with_id("call-run", "execute_cmd", {"command": "python script.py"})]},
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
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = asyncio.run(process_non_pipeline_command(
        user_message="prepare then run",
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

    assert sent == [("givi", "execute_cmd", {"command": f'& "{venv_python}" script.py'})]
    assert result["commands"][0]["tool_name"] == "device.prepare_runtime"
    assert result["commands"][1]["command"] == f'& "{venv_python}" script.py'


def test_write_content_and_execute_cmd_tool_log_types():
    write = asyncio.run(_run_non_pipeline("write_content", {"path": "C:/tmp/hello.txt", "content": "hello"}))
    execute = asyncio.run(_run_non_pipeline("execute_cmd", {"command": "whoami"}))

    assert write["commands"][0]["tool_name"] == "write_content"
    assert write["commands"][0]["tool_type"] == "typed"
    assert execute["commands"][0]["tool_name"] == "execute_cmd"
    assert execute["commands"][0]["tool_type"] == "fallback"


def test_non_pipeline_execute_cmd_and_write_content_honor_device_id_override():
    seen = []

    async def send_command_fn(device_id, action, params):
        seen.append((device_id, action))
        return {"returncode": 0, "stdout": "ok", "stderr": "", "path": params.get("path")}

    asyncio.run(_run_non_pipeline("execute_cmd", {"command": "whoami", "device_id": "desktop"}, send_command_fn=send_command_fn))
    asyncio.run(_run_non_pipeline("write_content", {"path": "C:/tmp/hello.txt", "content": "hello", "device_id": "desktop"}, send_command_fn=send_command_fn))

    assert seen == [("desktop", "execute_cmd"), ("desktop", "write_content")]


def test_pipeline_worker_toolset_exposes_demo_typed_tools_without_planner_tools():
    names = {tool["function"]["name"] for tool in WORKER_TOOLS}

    assert "system_list_tools" not in names
    assert "device_get_passport" not in names
    assert "device_activate" not in names
    assert "device_repair_activation" not in names
    assert "device_repair_runtime" not in names
    assert "device_refresh_state" in names
    assert "device_check_runtime" in names
    assert "device_prepare_runtime" in names
    assert "window_list" in names
    assert "window_find" in names
    assert "window_verify" in names
    assert "app_launch" in names
    assert "app_verify_launch" in names


def test_prompt_contains_tool_selection_policy():
    assert "Tool selection policy:" in SYSTEM_PROMPT_TEMPLATE
    assert "Use typed tools first" in SYSTEM_PROMPT_TEMPLATE
    assert "execute_cmd / PowerShell only as fallback" in SYSTEM_PROMPT_TEMPLATE
    assert "Do not assume device state" in SYSTEM_PROMPT_TEMPLATE
    assert "prefer device_prepare_runtime or device_check_runtime" in SYSTEM_PROMPT_TEMPLATE
    assert "use its venv_python path" in SYSTEM_PROMPT_TEMPLATE
    assert "GUI success means a matching window is found/visible or the process is alive" in SYSTEM_PROMPT_TEMPLATE
    assert "window_find" in SYSTEM_PROMPT_TEMPLATE
    assert "app_launch" in SYSTEM_PROMPT_TEMPLATE


def test_prompt_prefers_refresh_state_for_explicit_state_checks():
    assert "Проверь состояние" in SYSTEM_PROMPT_TEMPLATE
    assert "call device_refresh_state directly" in SYSTEM_PROMPT_TEMPLATE
    assert "Do not call only device_get_passport" in SYSTEM_PROMPT_TEMPLATE


def test_prompt_contains_device_inventory_wording_rule():
    assert "Never say \"в сети не обнаружено\"" in SYSTEM_PROMPT_TEMPLATE
    assert "Других подключённых к ИРУ устройств сейчас не вижу." in SYSTEM_PROMPT_TEMPLATE
    assert "only connected-to-IRU device" in SYSTEM_PROMPT_TEMPLATE


def test_prompt_contains_concise_success_answer_rule():
    assert "Concise final answer policy:" in SYSTEM_PROMPT_TEMPLATE
    assert "UI already shows used tools and technical details" in SYSTEM_PROMPT_TEMPLATE
    assert "Готово. Создал папку" in SYSTEM_PROMPT_TEMPLATE

import asyncio
import json

import httpx

from server.controller_pipeline import (
    _extract_python_interpreters,
    _verification_command_succeeded,
    build_pipeline_task_receipt,
    enforce_conversation_context_answer,
    process_pipeline_subagents,
    run_pipeline_worker,
)


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


def test_pipeline_recoverable_failure_later_verification_completes_with_recovery(monkeypatch):
    responses = [
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "goal": "create docx files",
                        "steps": [
                            {"title": "check python-docx", "instruction": "check dependency", "device_id": "device-a"},
                            {"title": "verify files", "instruction": "verify created files", "device_id": "device-a"},
                        ],
                    }),
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [_execute_call("call-1", 'python -c "import docx"')]},
            }]
        },
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_failure_call("call-step0-failure", "dependency missing", ["step_1"])]}}]},
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [_execute_call("call-2", "verify generated docx files")]},
            }]
        },
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_text_call("call-step1-answer", "files verified", ["step_1"])]}}]},
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_text_call("call-final", "Готово, файлы созданы и проверены.", ["step_3"])]}}]},
    ]
    captured_messages = []
    updates = []
    finished = []
    status_by_idx = {}

    monkeypatch.setattr("server.controller_pipeline.db.create_task", lambda **kwargs: 1)
    monkeypatch.setattr("server.controller_pipeline.db.update_step", lambda task_id, idx, status, summary=None: updates.append((idx, status, summary)) or status_by_idx.__setitem__(idx, status) or True)
    monkeypatch.setattr("server.controller_pipeline.db.finish_task", lambda task_id, status: finished.append(status) or True)
    monkeypatch.setattr("server.controller_pipeline.collect_tasks", lambda task_ids: [{
        "id": 1,
        "goal": "create docx files",
        "status": finished[-1] if finished else "running",
        "steps": [{"idx": idx, "status": status} for idx, status in sorted(status_by_idx.items())],
    }])
    monkeypatch.setattr("server.controller_pipeline.push_tasks_view", lambda *args, **kwargs: None)
    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", lambda device_id: None)
    monkeypatch.setattr("server.controller_pipeline.build_memory_block", lambda machine_guid, user_id: "")
    monkeypatch.setattr("server.controller_pipeline.db.add_command_memory", lambda **kwargs: None)

    async def _send_command_fn(device_id, action, params):
        command = params.get("command", "")
        if "import docx" in command:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "Traceback\nModuleNotFoundError: No module named 'docx'\n",
            }
        return {
            "returncode": 0,
            "stdout": "IRU_VERIFIED=1 7 files",
            "stderr": "",
            "files_verified": [r"C:\Users\russa\Desktop\test\file1.docx"],
            "artifacts_created": [r"C:\Users\russa\Desktop\test\file1.docx"],
        }

    result = asyncio.run(process_pipeline_subagents(
        user_message="create files",
        device_id="device-a",
        device_info={"hostname": "alpha", "os": "Windows", "os_version": "11"},
        all_devices={"device-a": {"info": {"hostname": "alpha", "os": "Windows", "os_version": "11"}}},
        send_command_fn=_send_command_fn,
        get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
        chat_history=[{"role": "user", "content": "create files"}],
        user_id=1,
        chat_id=1,
        device_profile=None,
        modes={},
        poll_task_id=None,
        load_llm_config_fn=lambda: {"model": "mock-model", "max_tokens": 1000},
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=_completion_fn(responses, captured_messages),
        worker_tools=[],
        windows_rules="windows rules",
        linux_rules="linux rules",
    ))

    assert finished == ["completed_with_recovery"]
    assert (0, "recovered") in [(idx, status) for idx, status, _summary in updates]
    assert result["tasks"][0]["status"] == "completed_with_recovery"
    assert result["task_receipt"]["task_status"] == "completed_with_recovery"
    assert result["task_receipt"]["artifacts_created"] == [r"C:\Users\russa\Desktop\test\file1.docx"]
    assert result["task_receipt"]["project_path"] == r"C:\Users\russa\Desktop\test"
    assert result["task_receipt"]["created_files"] == [r"C:\Users\russa\Desktop\test\file1.docx"]
    assert result["task_receipt"]["created_file_evidence"] == [{
        "path": r"C:\Users\russa\Desktop\test\file1.docx",
        "step_id": "step_3",
        "tool_name": "execute_cmd",
    }]
    assert result["task_receipt"]["files_verified"] == [r"C:\Users\russa\Desktop\test\file1.docx"]
    assert result["task_receipt"]["recoveries_applied"][0]["step_index"] == 0
    assert result["task_receipt"]["commands_failed"][0]["error_type"] == "dependency_missing"
    assert any("ModuleNotFoundError" in (cmd.get("result", {}).get("stderr") or "") for cmd in result["commands"])


def test_pipeline_dispatches_advertised_device_runtime_tool(monkeypatch):
    responses = [
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "goal": "check runtime",
                        "steps": [
                            {"title": "check runtime", "instruction": "check managed runtime", "device_id": "device-a"},
                        ],
                    }),
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call-runtime",
                        "function": {"name": "device_check_runtime", "arguments": json.dumps({})},
                    }],
                },
            }]
        },
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_text_call("call-step-answer", "runtime checked", ["step_1"])]}}]},
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_text_call("call-final", "Runtime checked.", ["step_1"])]}}]},
    ]
    captured_messages = []
    finished = []
    status_by_idx = {}
    device_calls = []

    monkeypatch.setattr("server.controller_pipeline.db.create_task", lambda **kwargs: 1)
    monkeypatch.setattr("server.controller_pipeline.db.update_step", lambda task_id, idx, status, summary=None: status_by_idx.__setitem__(idx, status) or True)
    monkeypatch.setattr("server.controller_pipeline.db.finish_task", lambda task_id, status: finished.append(status) or True)
    monkeypatch.setattr("server.controller_pipeline.collect_tasks", lambda task_ids: [{
        "id": 1,
        "goal": "check runtime",
        "status": finished[-1] if finished else "running",
        "steps": [{"idx": idx, "status": status} for idx, status in sorted(status_by_idx.items())],
    }])
    monkeypatch.setattr("server.controller_pipeline.push_tasks_view", lambda *args, **kwargs: None)
    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", lambda device_id: None)
    monkeypatch.setattr("server.controller_pipeline.build_memory_block", lambda machine_guid, user_id: "")
    monkeypatch.setattr("server.controller_pipeline.db.add_command_memory", lambda **kwargs: None)

    async def _send_command_fn(device_id, action, params):
        raise AssertionError(f"device_check_runtime should not go through send_command_fn: {action}")

    async def _device_tool_fn(name, args):
        device_calls.append((name, args["device_id"]))
        return {
            "status": "ok",
            "device_id": args["device_id"],
            "runtime_summary": {"runtime_status": "ok", "python_version": "3.11.9", "pip_status": "ok"},
        }

    result = asyncio.run(process_pipeline_subagents(
        user_message="check runtime",
        device_id="device-a",
        device_info={"hostname": "alpha", "os": "Windows", "os_version": "11"},
        all_devices={"device-a": {"info": {"hostname": "alpha", "os": "Windows", "os_version": "11"}}},
        send_command_fn=_send_command_fn,
        get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
        chat_history=[{"role": "user", "content": "check runtime"}],
        user_id=1,
        chat_id=1,
        device_profile=None,
        modes={},
        poll_task_id=None,
        load_llm_config_fn=lambda: {"model": "mock-model", "max_tokens": 1000, "answer_auditor_enabled": False},
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=_completion_fn(responses, captured_messages),
        worker_tools=[],
        windows_rules="windows rules",
        linux_rules="linux rules",
        device_tool_fn=_device_tool_fn,
    ))

    assert device_calls == [("device_check_runtime", "device-a")]
    assert finished == ["completed"]
    runtime_command = result["commands"][0]
    assert runtime_command["action"] == "device_check_runtime"
    assert runtime_command["tool_name"] == "device.check_runtime"
    assert runtime_command["tool_type"] == "typed"
    assert runtime_command["result"]["runtime_summary"]["runtime_status"] == "ok"
    assert "Неизвестная функция" not in json.dumps(result["commands"], ensure_ascii=False)


def test_pipeline_worker_blocks_broad_desktop_scan_when_created_files_are_known(monkeypatch):
    responses = [
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [_execute_call(
                        "call-broad-scan",
                        r'Get-ChildItem -Path "C:\Users\russa\Desktop" -Recurse -Filter *.docx',
                    )],
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [_execute_call(
                        "call-open-exact",
                        r'Start-Process "C:\Users\russa\Desktop\CourseLaunchKit\description.docx"',
                    )],
                },
            }]
        },
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [_answer_text_call("call-answer", "opened exact file", ["step_2"])]}}]},
    ]
    captured_messages = []
    sent = []

    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", lambda device_id: None)
    monkeypatch.setattr("server.controller_pipeline.db.add_command_memory", lambda **kwargs: None)

    async def _chat_completion_request_fn(**kwargs):
        captured_messages.append(kwargs["messages"])
        assert responses, "Unexpected LLM call"
        return responses.pop(0)

    async def _send_command_fn(device_id, action, params):
        sent.append((device_id, action, params["command"]))
        assert "Get-ChildItem" not in params["command"]
        return {"returncode": 0, "stdout": "opened", "stderr": ""}

    shared = {
        "current_device_id": "device-a",
        "current_hostname": "alpha",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "other_devices_summary": "",
        "target_device_id": "device-a",
        "os_rules": "",
        "current_datetime_msk": "2026-05-25 12:00",
        "recent_artifact_context": {
            "project_path": r"C:\Users\russa\Desktop\CourseLaunchKit",
            "created_files": [
                {"path": r"C:\Users\russa\Desktop\CourseLaunchKit\description.docx", "step_id": "old_step", "tool_name": "execute_cmd"},
            ],
        },
    }

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model", "answer_auditor_enabled": False},
                model="mock-model",
                shared=shared,
                overall_goal="open created documents",
                step={"title": "open docs", "instruction": "open known files", "device_id": "device-a"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=_send_command_fn,
                get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_chat_completion_request_fn,
                worker_tools=[],
            )

    result = asyncio.run(_run())

    assert result["status"] == "ok"
    assert sent == [(
        "device-a",
        "execute_cmd",
        r'Start-Process "C:\Users\russa\Desktop\CourseLaunchKit\description.docx"',
    )]
    assert result["commands"][0]["status"] == "blocked"
    assert result["commands"][0]["result"]["policy"] == "recent_artifact_scope"
    assert "Broad recursive Desktop scan" in result["commands"][0]["result"]["error"]
    assert result["commands"][1]["command"] == r'Start-Process "C:\Users\russa\Desktop\CourseLaunchKit\description.docx"'


def test_receipt_warns_on_multiple_python_interpreters():
    receipt = build_pipeline_task_receipt(
        task_status="completed_with_recovery",
        commands=[
            {"action": "execute_cmd", "command": r'& "C:\Python311\python.exe" -V', "status": "success", "result": {"returncode": 0}},
            {"action": "execute_cmd", "command": r'& "D:\Tools\Python312\python.exe" -V', "status": "success", "result": {"returncode": 0}},
        ],
        step_results=[{"idx": 0, "title": "run", "status": "recovered"}],
        recovery_warnings=[],
        receipt_dicts=[],
    )

    assert "multiple_python_interpreters_used" in receipt["warnings"]
    assert len(receipt["python_interpreters"]) == 2


def test_not_ok_is_not_verification_success():
    assert _verification_command_succeeded({
        "action": "execute_cmd",
        "command": "verify",
        "status": "success",
        "result": {"returncode": 0, "stdout": "NOT OK", "stderr": ""},
    }) is False


def test_strict_iru_verified_marker_is_verification_success():
    assert _verification_command_succeeded({
        "action": "execute_cmd",
        "command": "verify",
        "status": "success",
        "result": {"returncode": 0, "stdout": "IRU_VERIFIED=1", "stderr": ""},
    }) is True


def test_complex_bare_python_command_records_bare_interpreter_not_first_token():
    interpreters = _extract_python_interpreters(
        [{
            "action": "execute_cmd",
            "command": r'Set-Location "C:\work"; python main.py',
            "status": "success",
            "result": {"returncode": 0},
        }],
        [],
    )

    assert {"path": "python", "version": None, "source": "bare_command"} in interpreters
    assert all(item["path"] != "Set-Location" for item in interpreters)


def test_conversation_context_replaces_false_first_request_claim():
    answer = enforce_conversation_context_answer(
        "Это первый запрос в сессии, предыдущих сообщений нет.",
        {
            "history_available": True,
            "recent_turns_count": 2,
            "previous_user_message": "Создай папку тест",
            "previous_assistant_message": "Файлы созданы.",
        },
    )

    assert "Создай папку тест" in answer
    assert "Файлы созданы" in answer


def test_history_unavailable_does_not_claim_first_request():
    answer = enforce_conversation_context_answer(
        "Это первый запрос в сессии.",
        {
            "history_available": False,
            "recent_turns_count": 0,
            "previous_user_message": None,
            "previous_assistant_message": None,
        },
    )

    assert answer == "В этом режиме выполнения история недоступна."
    assert "первый запрос" not in answer.lower()

import asyncio
import json

from server.controller_pipeline import (
    build_pipeline_task_receipt,
    enforce_conversation_context_answer,
    process_pipeline_subagents,
)


def _execute_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
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
        {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [_execute_call("call-2", "verify generated docx files")]},
            }]
        },
        {"choices": [{"finish_reason": "stop", "message": {"content": "files verified"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "Готово, файлы созданы и проверены."}}]},
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
            "stdout": "VERIFIED 7 files",
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
    assert result["task_receipt"]["files_verified"] == [r"C:\Users\russa\Desktop\test\file1.docx"]
    assert result["task_receipt"]["recoveries_applied"][0]["step_index"] == 0
    assert result["task_receipt"]["commands_failed"][0]["error_type"] == "dependency_missing"
    assert any("ModuleNotFoundError" in (cmd.get("result", {}).get("stderr") or "") for cmd in result["commands"])


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

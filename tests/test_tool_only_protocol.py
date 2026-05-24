import asyncio
import json

import server.controller_non_pipeline as controller_non_pipeline
import server.controller_pipeline as controller_pipeline
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_pipeline import process_pipeline_subagents
from server.run_journal import validate_answer_text_payload
from server.tool_registry import canonical_tool_name, list_tools


def _tool_call(call_id: str, name: str, args: dict | None = None) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}, ensure_ascii=False),
        },
    }


def _answer_payload(text: str = "ok", *, answer_type: str = "pure_text", basis: list[str] | None = None) -> dict:
    basis = basis or []
    grounded = answer_type == "grounded_report"
    return {
        "answer_type": answer_type,
        "text": text,
        "basis": basis,
        "self_check": {
            "depends_on_current_external_state": grounded,
            "claims_completed_action": grounded,
            "has_sufficient_evidence": True,
            "missing_evidence_question": "",
        },
    }


def _answer_call(call_id: str, text: str = "ok", *, answer_type: str = "pure_text", basis: list[str] | None = None) -> dict:
    return _tool_call(call_id, "answer_text", _answer_payload(text, answer_type=answer_type, basis=basis))


def _execute_call(call_id: str, command: str) -> dict:
    return _tool_call(call_id, "execute_cmd", {"command": command})


def _completion_fn(responses, captured=None):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        assert queue, "Unexpected LLM call"
        return queue.pop(0)

    return _chat_completion_request_fn


def _message(content="", tool_calls=None, finish_reason="tool_calls"):
    msg = {"content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"finish_reason": finish_reason, "message": msg}]}


def _run_case(responses, *, send_command_fn=None, captured=None, chat_history=None, cfg=None):
    async def _send(device_id, action, params):
        if send_command_fn:
            return await send_command_fn(device_id, action, params)
        return {"status": "ok", "returncode": 0, "stdout": "ok", "stderr": "", "path": params.get("path")}

    return asyncio.run(process_non_pipeline_command(
        user_message="Задача",
        device_id="device-1",
        device_info={"hostname": "devbox", "os": "Windows"},
        send_command_fn=_send,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=chat_history or [],
        user_id=None,
        chat_id=None,
        modes={},
        poll_task_id=None,
        cfg=cfg or {"model": "mock-model", "max_tokens": 512, "answer_auditor_enabled": False},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=6,
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=_completion_fn(responses, captured),
    ))


def test_raw_conceptual_answer_is_rejected_then_answer_text_succeeds():
    captured = []
    result = _run_case([
        _message("Tool Registry нужен для выбора инструментов.", tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_answer_call("call-answer", "Tool Registry нужен для выбора инструментов.")]),
    ], captured=captured)

    assert result["answer"] == "Tool Registry нужен для выбора инструментов."
    assert any("Raw assistant content is not allowed" in msg["content"] for msg in captured[1]["messages"])
    assert result["commands"][0]["tool_name"] == "answer.text"


def test_conceptual_answer_through_answer_text_succeeds_without_external_tool():
    result = _run_case([
        _message(tool_calls=[_answer_call("call-answer", "Tool Registry группирует доступные возможности.")]),
    ])

    assert result["answer"] == "Tool Registry группирует доступные возможности."
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["answer.text"]


def test_non_pipeline_answer_text_returns_validated_payload_unchanged(monkeypatch):
    def _legacy_trust_should_not_run(answer, commands):
        raise AssertionError("legacy enforce_trusted_answer must not run for answer_text")

    monkeypatch.setattr(controller_non_pipeline, "enforce_trusted_answer", _legacy_trust_should_not_run, raising=False)
    text = "Keep this exact answer, including failed/error words and /api/download/not_allowed"

    result = _run_case([
        _message(tool_calls=[_answer_call("call-answer", text)]),
    ])

    assert result["answer"] == text


def test_window_check_raw_text_rejected_then_requires_current_step_basis():
    sent = []

    async def _send(device_id, action, params):
        sent.append((device_id, action, params))
        return {"status": "not_found", "matches": []}

    result = _run_case([
        _message("Проверил, Блокнот не открыт", tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Блокнот"})]),
        _message(tool_calls=[_answer_call("call-answer", "Блокнот сейчас не найден.", answer_type="grounded_report", basis=["step_1"])]),
    ], send_command_fn=_send)

    assert sent == [("device-1", "window.find", {"title_contains": "Блокнот"})]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["window.find", "answer.text"]
    assert result["commands"][0]["step_id"] == "step_1"
    assert result["commands"][1]["result"]["basis"] == ["step_1"]


def test_repeated_question_old_or_missing_basis_is_rejected_then_fresh_tool_runs():
    sent = []

    async def _send(device_id, action, params):
        sent.append(action)
        return {"status": "not_found", "matches": []}

    result = _run_case([
        _message(tool_calls=[_answer_call("call-stale", "Блокнот не открыт.", answer_type="grounded_report", basis=["step_99"])]),
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Блокнот"})]),
        _message(tool_calls=[_answer_call("call-answer", "Блокнот сейчас не найден.", answer_type="grounded_report", basis=["step_1"])]),
    ], send_command_fn=_send, chat_history=[{"role": "assistant", "content": "previous step_99"}])

    assert sent == ["window.find"]
    assert result["commands"][0]["tool_name"] == "window.find"
    assert result["answer"] == "Блокнот сейчас не найден."


def test_file_creation_raw_success_rejected_then_write_content_basis_accepted():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, params["path"]))
        return {"status": "ok", "path": params["path"]}

    result = _run_case([
        _message("Готово, файл создан", tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_tool_call("call-write", "write_content", {"path": "C:/Temp/hello.txt", "content": "hello"})]),
        _message(tool_calls=[_answer_call("call-answer", "Файл создан.", answer_type="grounded_report", basis=["step_1"])]),
    ], send_command_fn=_send)

    assert sent == [("write_content", "C:/Temp/hello.txt")]
    assert result["commands"][0]["tool_name"] == "write_content"
    assert result["commands"][1]["tool_name"] == "answer.text"


def test_grounded_report_empty_basis_rejected():
    result = _run_case([
        _message(tool_calls=[_answer_call("call-bad", "Готово.", answer_type="grounded_report", basis=[])]),
        _message(tool_calls=[_answer_call("call-good", "Нужен инструмент для проверки.", answer_type="pure_text")]),
    ])

    assert result["answer"] == "Нужен инструмент для проверки."
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["answer.text"]


def test_basis_must_be_current_step_id_not_tool_name():
    try:
        validate_answer_text_payload(_answer_payload("bad", answer_type="grounded_report", basis=["window.find"]), [])
    except Exception as exc:
        assert "basis" in str(exc)
    else:
        raise AssertionError("basis with tool name should be rejected")


def test_answer_report_failure_and_clarification_are_terminal():
    failure = _run_case([
        _message(tool_calls=[_tool_call("call-failure", "answer_report_failure", {
            "message": "Не удалось выполнить задачу.",
            "reason": "server-side policy failure before any tool",
            "recoverable": False,
            "suggested_next_action": "Повторить запрос позже.",
            "basis": [],
        })]),
    ])
    clarification = _run_case([
        _message(tool_calls=[_tool_call("call-clarify", "answer_ask_clarification", {
            "question": "Какой файл открыть?",
            "reason": "Не указан путь.",
            "options": ["Указать путь"],
        })]),
    ])

    assert failure["answer"] == "Не удалось выполнить задачу."
    assert failure["commands"][0]["tool_name"] == "answer.report_failure"
    assert clarification["answer"] == "Какой файл открыть?"
    assert clarification["commands"][0]["tool_name"] == "answer.ask_clarification"


def test_multi_tool_batch_rejected_and_executes_none():
    sent = []

    async def _send(device_id, action, params):
        sent.append(action)
        return {"status": "ok"}

    result = _run_case([
        _message(tool_calls=[
            _tool_call("call-window", "window_find", {"title_contains": "Блокнот"}),
            _answer_call("call-answer", "Блокнот не открыт.", answer_type="grounded_report", basis=["step_1"]),
        ]),
        _message(tool_calls=[_answer_call("call-final", "Нужно вызвать один инструмент за итерацию.")]),
    ], send_command_fn=_send)

    assert sent == []
    assert result["answer"] == "Нужно вызвать один инструмент за итерацию."


def test_tool_result_message_contains_step_wrapper():
    captured = []

    async def _send(device_id, action, params):
        return {"status": "not_found", "matches": []}

    _run_case([
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Блокнот"})]),
        _message(tool_calls=[_answer_call("call-answer", "Блокнот не найден.", answer_type="grounded_report", basis=["step_1"])]),
    ], send_command_fn=_send, captured=captured)

    tool_messages = [msg for msg in captured[1]["messages"] if msg.get("role") == "tool"]
    wrapped = json.loads(tool_messages[-1]["content"])
    assert wrapped["step_id"] == "step_1"
    assert wrapped["tool_name"] == "window.find"
    assert "summary" in wrapped
    assert "result" in wrapped


def test_tool_registry_answer_tools_exist_and_canonical_names_map():
    tools = list_tools("answer")
    names = {tool["name"] for tool in tools["answer"]}

    assert {"answer.text", "answer.ask_clarification", "answer.report_failure", "answer.request_confirmation"} <= names
    assert canonical_tool_name("answer_text") == "answer.text"
    assert canonical_tool_name("answer_report_failure") == "answer.report_failure"


def test_auditor_rejects_invalid_answer_and_retry_succeeds():
    captured = []

    async def _chat_completion_request_fn(**kwargs):
        captured.append(kwargs)
        if kwargs.get("tools") is None:
            if len([item for item in captured if item.get("tools") is None]) > 1:
                return _message(json.dumps({"valid": True, "reason": "conceptual caveat"}), tool_calls=None, finish_reason="stop")
            return _message(json.dumps({"valid": False, "reason": "external state without basis"}), tool_calls=None, finish_reason="stop")
        if len([msg for msg in kwargs["messages"] if "not grounded" in msg.get("content", "")]) == 0:
            return _message(tool_calls=[_answer_call("call-bad", "Блокнот не открыт.")])
        return _message(tool_calls=[_answer_call("call-good", "Я не проверял текущее состояние.")])

    result = asyncio.run(process_non_pipeline_command(
        user_message="Проверь Блокнот",
        device_id="device-1",
        device_info={"hostname": "devbox", "os": "Windows"},
        send_command_fn=lambda device_id, action, params: {"status": "ok"},
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=None,
        chat_id=None,
        modes={},
        poll_task_id=None,
        cfg={"model": "real-ish", "max_tokens": 512, "answer_auditor_enabled": True},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=5,
        pick_model_fn=lambda cfg, modes: "real-ish",
        chat_completion_request_fn=_chat_completion_request_fn,
    ))

    assert result["answer"] == "Я не проверял текущее состояние."
    assert any(kwargs.get("tools") is None for kwargs in captured)


def test_pipeline_final_raw_summary_rejected_then_answer_text(monkeypatch):
    def _legacy_trust_should_not_run(answer, commands):
        raise AssertionError("legacy enforce_trusted_answer must not run for pipeline answer_text")

    monkeypatch.setattr(controller_pipeline, "enforce_trusted_answer", _legacy_trust_should_not_run)
    final_text = "pipeline ok with failed/error words and /api/download/not_allowed"
    responses = [
        _message(json.dumps({
            "goal": "verify task",
            "steps": [{"title": "run check", "instruction": "run check", "device_id": "device-1"}],
        }), tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_execute_call("call-exec", "echo ok")]),
        _message(tool_calls=[_answer_call("call-step-answer", "step ok", answer_type="grounded_report", basis=["step_1"])]),
        _message("raw final summary", tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_answer_call("call-final", final_text, answer_type="grounded_report", basis=["step_1"])]),
    ]
    captured = []
    finished = []
    step_status = {}

    monkeypatch.setattr("server.controller_pipeline.db.create_task", lambda **kwargs: 1)
    monkeypatch.setattr("server.controller_pipeline.db.update_step", lambda task_id, idx, status, summary=None: step_status.__setitem__(idx, status) or True)
    monkeypatch.setattr("server.controller_pipeline.db.finish_task", lambda task_id, status: finished.append(status) or True)
    monkeypatch.setattr("server.controller_pipeline.collect_tasks", lambda task_ids: [{
        "id": 1,
        "goal": "verify task",
        "status": finished[-1] if finished else "running",
        "steps": [{"idx": idx, "status": status} for idx, status in sorted(step_status.items())],
    }])
    monkeypatch.setattr("server.controller_pipeline.push_tasks_view", lambda *args, **kwargs: None)
    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", lambda device_id: None)
    monkeypatch.setattr("server.controller_pipeline.build_memory_block", lambda machine_guid, user_id: "")
    monkeypatch.setattr("server.controller_pipeline.db.add_command_memory", lambda **kwargs: None)

    async def _send(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = asyncio.run(process_pipeline_subagents(
        user_message="verify task",
        device_id="device-1",
        device_info={"hostname": "devbox", "os": "Windows"},
        all_devices={"device-1": {"info": {"hostname": "devbox", "os": "Windows"}}},
        send_command_fn=_send,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=1,
        chat_id=1,
        device_profile=None,
        modes={},
        poll_task_id=None,
        load_llm_config_fn=lambda: {"model": "mock-model", "max_tokens": 1000, "answer_auditor_enabled": False},
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=_completion_fn(responses, captured),
        worker_tools=[],
        windows_rules="windows rules",
        linux_rules="linux rules",
    ))

    assert result["answer"] == final_text
    assert finished == ["completed"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["execute_cmd", "answer.text", "answer.text"]
    assert any("Raw assistant content is not allowed" in msg.get("content", "") for msg in captured[-1]["messages"])

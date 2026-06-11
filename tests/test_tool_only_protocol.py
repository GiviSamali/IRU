import asyncio
import json

import server.controller_non_pipeline as controller_non_pipeline
import server.controller_pipeline as controller_pipeline
import server.runtime_state as runtime_state
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_pipeline import process_pipeline_subagents, run_pipeline_worker
from server.run_journal import validate_answer_text_payload, wrap_tool_result_for_llm
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


def _run_case(
    responses,
    *,
    user_message="Задача",
    send_command_fn=None,
    captured=None,
    chat_history=None,
    cfg=None,
    user_id=None,
    chat_id=None,
    mem_user_id=None,
    device_id="device-1",
    poll_task_id=None,
    max_iterations=6,
    device_tool_fn=None,
):
    async def _send(device_id, action, params):
        if send_command_fn:
            return await send_command_fn(device_id, action, params)
        return {"status": "ok", "returncode": 0, "stdout": "ok", "stderr": "", "path": params.get("path")}

    return asyncio.run(process_non_pipeline_command(
        user_message=user_message,
        device_id=device_id,
        device_info={"hostname": "devbox", "os": "Windows"},
        send_command_fn=_send,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=chat_history or [],
        user_id=user_id,
        chat_id=chat_id,
        modes={},
        poll_task_id=poll_task_id,
        cfg=cfg or {"model": "mock-model", "max_tokens": 512, "answer_auditor_enabled": False},
        system_msg="system",
        machine_guid=None,
        mem_user_id=mem_user_id,
        non_pipeline_tools=[],
        max_iterations=max_iterations,
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=_completion_fn(responses, captured),
        device_tool_fn=device_tool_fn,
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


def test_non_pipeline_cancelled_task_stops_before_next_tool():
    import server.runtime_state as runtime_state

    runtime_state.tasks["cancel-test"] = {
        "task_id": "cancel-test",
        "user_id": 1,
        "status": "cancelling",
        "cancel_requested": True,
    }
    captured = []

    result = _run_case(
        [_message(tool_calls=[_execute_call("call-exec", "echo should-not-run")])],
        captured=captured,
        user_id=1,
        poll_task_id="cancel-test",
    )

    assert captured == []
    assert result["cancelled"] is True
    assert result["answer"] == "Остановлено пользователем."
    assert result["commands"][0]["tool_name"] == "task.cancel"
    assert result["commands"][0]["status"] == "cancelled"


def test_pipeline_worker_cancelled_task_stops_before_llm_and_tool(monkeypatch):
    monkeypatch.setattr(controller_pipeline, "is_task_cancel_requested", lambda task_id: task_id == "pipeline-cancel")

    async def _completion_should_not_run(**kwargs):
        raise AssertionError("pipeline worker must not call LLM after cancellation")

    async def _send_should_not_run(device_id, action, params):
        raise AssertionError("pipeline worker must not run tools after cancellation")

    result = asyncio.run(run_pipeline_worker(
        client=None,
        cfg={"model": "mock-model"},
        model="mock-model",
        shared={
            "current_device_id": "device-1",
            "target_device_id": "device-1",
            "current_hostname": "devbox",
        },
        overall_goal="goal",
        step={"id": "s1", "title": "Step 1", "instruction": "Do work"},
        completed_steps=[],
        chat_history=[],
        send_command_fn=_send_should_not_run,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        machine_guid=None,
        mem_user_id=None,
        poll_task_id="pipeline-cancel",
        step_index=0,
        chat_completion_request_fn=_completion_should_not_run,
        worker_tools=[],
    ))

    assert result["status"] == "cancelled"
    assert result["commands"][0]["tool_name"] == "task.cancel"
    assert result["commands"][0]["status"] == "cancelled"


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

def test_write_content_large_payload_is_compacted_in_journal_and_llm_result():
    large_content = "<html>\n" + ("A" * 6000) + "\n</html>"
    sent = []
    captured = []

    async def _send(device_id, action, params):
        sent.append((action, params["path"], len(params["content"])))
        return {
            "status": "ok",
            "path": params["path"],
            "bytes_written": len(params["content"].encode("utf-8")),
            "total_size": len(params["content"].encode("utf-8")),
        }

    result = _run_case([
        _message(tool_calls=[_tool_call("call-write", "write_content", {
            "path": "C:/Temp/large.html",
            "content": large_content,
        })]),
        _message(tool_calls=[_answer_call("call-answer", "Файл создан.", answer_type="grounded_report", basis=["step_1"])]),
    ], send_command_fn=_send, captured=captured)

    assert sent == [("write_content", "C:/Temp/large.html", len(large_content))]
    command = result["commands"][0]
    dumped_command = json.dumps(command, ensure_ascii=False)
    assert large_content not in dumped_command
    assert "A" * 5000 not in dumped_command
    assert command["command"] == "[write] C:/Temp/large.html"
    assert command["result"]["path"] == "C:/Temp/large.html"
    assert command["result"]["chars_written"] == len(large_content)
    assert command["result"]["bytes_written"] == len(large_content.encode("utf-8"))
    assert len(command["result"]["content_preview"]) <= 120
    assert len(command["result"]["content_sha256"]) == 64
    assert command["result"]["summary"].startswith("OK: file_written")

    wrapped = wrap_tool_result_for_llm(command)
    dumped_wrapped = json.dumps(wrapped, ensure_ascii=False)
    assert large_content not in dumped_wrapped
    assert "A" * 5000 not in dumped_wrapped
    assert wrapped["result"]["content_sha256"] == command["result"]["content_sha256"]

    tool_messages = [msg for msg in captured[1]["messages"] if msg.get("role") == "tool"]
    assert tool_messages
    assert large_content not in tool_messages[-1]["content"]
    assert "A" * 5000 not in tool_messages[-1]["content"]


def test_write_content_error_basis_blocks_completed_success_claim():
    async def _send(device_id, action, params):
        return {"status": "error", "path": params["path"], "error": "disk full"}

    error_payload = {
        "answer_type": "error_report",
        "text": "Не удалось записать файл: disk full.",
        "basis": ["step_1"],
        "self_check": {
            "depends_on_current_external_state": True,
            "claims_completed_action": False,
            "has_sufficient_evidence": False,
            "missing_evidence_question": "write_content returned ERROR.",
        },
    }

    result = _run_case([
        _message(tool_calls=[_tool_call("call-write", "write_content", {
            "path": "C:/Temp/fail.txt",
            "content": "hello",
        })]),
        _message(tool_calls=[_answer_call("call-bad", "Файл создан.", answer_type="grounded_report", basis=["step_1"])]),
        _message(tool_calls=[_tool_call("call-good", "answer_text", error_payload)]),
    ], send_command_fn=_send)

    assert [cmd["tool_name"] for cmd in result["commands"]] == ["write_content", "answer.text"]
    assert result["commands"][0]["status"] == "failed"
    assert result["commands"][0]["result"]["summary"].startswith("ERROR:")
    assert result["answer"] == "Не удалось записать файл: disk full."


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


def test_tool_registry_memory_tools_exist_and_canonical_names_map():
    tools = list_tools("memory")
    names = {tool["name"] for tool in tools["memory"]}

    assert {"memory.get_stats", "memory.list_facts"} <= names
    assert canonical_tool_name("memory_get_stats") == "memory.get_stats"
    assert canonical_tool_name("memory_list_facts") == "memory.list_facts"


def test_memory_list_facts_tool_only_run_uses_current_step_basis(monkeypatch):
    calls = []

    def fake_run_memory_tool(tool_name, args, *, user_id):
        calls.append((tool_name, args, user_id))
        return {
            "status": "ok",
            "source": "server_user_memory",
            "facts_count": 1,
            "facts": [{"id": 7, "text": "likes typed tools", "category": "preference", "source": "user"}],
        }

    async def _send(device_id, action, params):
        raise AssertionError("memory tools must not call device command execution")

    monkeypatch.setattr(controller_non_pipeline, "run_memory_tool", fake_run_memory_tool)

    result = _run_case([
        _message(tool_calls=[_tool_call("call-memory", "memory_list_facts", {"limit": 10})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "В памяти 1 факт: likes typed tools.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send, mem_user_id="user-1", device_id="")

    assert calls == [("memory_list_facts", {"limit": 10}, "user-1")]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["memory.list_facts", "answer.text"]
    assert result["commands"][0]["step_id"] == "step_1"
    assert result["commands"][1]["result"]["basis"] == ["step_1"]
    assert all(cmd["tool_name"] not in {"execute_cmd", "device.get_passport"} for cmd in result["commands"])


def test_duplicate_system_list_tools_is_guarded_then_answer_text(monkeypatch):
    calls = []
    captured = []

    def fake_list_tools(category="all"):
        calls.append(category)
        return {"system": [{"name": "system.list_tools"}]}

    monkeypatch.setattr(controller_non_pipeline, "list_tools", fake_list_tools)

    result = _run_case([
        _message(tool_calls=[_tool_call("call-tools-1", "system_list_tools", {})]),
        _message(tool_calls=[_tool_call("call-tools-2", "system_list_tools", {"category": "all"})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Tool list is available.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], captured=captured)

    assert calls == ["all"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["system.list_tools", "answer.text"]
    duplicate_messages = [
        json.loads(msg["content"])
        for msg in captured[2]["messages"]
        if msg.get("role") == "tool"
        and "duplicate_read_only_tool_call" in msg.get("content", "")
    ]
    assert duplicate_messages[-1]["previous_step_id"] == "step_1"
    assert "Call answer_text" in captured[2]["messages"][-1]["content"]


def test_duplicate_memory_list_facts_is_guarded_then_answer_text(monkeypatch):
    calls = []

    def fake_run_memory_tool(tool_name, args, *, user_id):
        calls.append((tool_name, args, user_id))
        return {
            "status": "ok",
            "source": "server_user_memory",
            "facts_count": 1,
            "facts": [{"id": 1, "text": "remembered fact", "source": "user"}],
        }

    monkeypatch.setattr(controller_non_pipeline, "run_memory_tool", fake_run_memory_tool)

    result = _run_case([
        _message(tool_calls=[_tool_call("call-memory-1", "memory_list_facts", {})]),
        _message(tool_calls=[_tool_call("call-memory-2", "memory_list_facts", {"limit": 20})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "There is one remembered fact.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], mem_user_id="user-1")

    assert calls == [("memory_list_facts", {}, "user-1")]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["memory.list_facts", "answer.text"]


def test_system_list_tools_grounded_answer_cannot_add_hidden_tools():
    result = _run_case([
        _message(tool_calls=[_tool_call("call-tools", "system_list_tools", {"category": "all"})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Доступны system.list_tools, get_file_link и window.screencapture. Насчёт вкладок: попробую открыть.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ])

    assert [cmd["tool_name"] for cmd in result["commands"]] == ["system.list_tools", "answer.text"]
    assert "get_file_link" not in result["answer"]
    assert "window.screencapture" not in result["answer"]
    assert "Насчёт вкладок" not in result["answer"]
    public_names = {
        tool["name"]
        for tools in list_tools("all").values()
        for tool in tools
    }
    for token in result["answer"].replace(",", " ").split():
        if "." in token or "_" in token:
            clean = token.strip(":-")
            assert clean in public_names or clean in {"Доступные", "инструменты"}


def test_get_last_run_summary_explains_previous_failure_without_retry():
    runtime_state.tasks["previous-failed"] = {
        "task_id": "previous-failed",
        "user_id": 42,
        "chat_id": 77,
        "message": "open url",
        "status": "error",
        "answer": "model did not choose an answer tool before max_iterations",
        "commands": [
            {
                "action": "execute_cmd",
                "tool_name": "execute_cmd",
                "step_id": "old_step_1",
                "status": "success",
                "result": {"returncode": 0, "stdout": "started"},
                "summary": "browser start command succeeded",
            },
            {
                "action": "answer_repair",
                "tool_name": "answer_repair",
                "step_id": "old_step_2",
                "status": "failed",
                "result": {"error": "model did not choose an answer tool before max_iterations"},
            },
        ],
        "created_at": 1000,
    }
    runtime_state.tasks["current-running"] = {
        "task_id": "current-running",
        "user_id": 42,
        "chat_id": 77,
        "message": "Что случилось?",
        "status": "running",
        "commands": [],
        "created_at": 2000,
    }

    async def _send(device_id, action, params):
        raise AssertionError("what happened flow must not retry device actions")

    result = _run_case([
        _message(tool_calls=[_tool_call("call-last", "system_get_last_run_summary", {})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Предыдущий запуск частично сработал: execute_cmd стартовал браузер, но финальный answer.text не был выбран до лимита.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send, user_id=42, poll_task_id="current-running", chat_id=77)

    assert [cmd["tool_name"] for cmd in result["commands"]] == ["system.get_last_run_summary", "answer.text"]
    summary = result["commands"][0]["result"]
    assert summary["last_task_id"] == "previous-failed"
    assert summary["partial_success_likely"] is True
    assert "execute_cmd" in summary["used_tools"]
    assert "answer_repair" in summary["failed_tools"]


def test_app_open_url_partial_focus_failure_terminates_with_answer_text():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        assert action == "app.open_url"
        return {
            "status": "opened_visible_focus_failed",
            "url": params["url"],
            "launched": True,
            "process_name": "msedge.exe",
            "pid": 123,
            "window_found": True,
            "window_title": "IRU Landing - Edge",
            "focus_status": "failed",
            "window": {"title": "IRU Landing - Edge", "pid": 123},
        }

    result = _run_case([
        _message(tool_calls=[_tool_call("call-open", "app_open_url", {"url": "https://irumode.online/", "browser": "edge", "focus": True})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Ссылка открыта, окно найдено, но сфокусировать окно не удалось.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send)

    assert sent == [("app.open_url", {"url": "https://irumode.online/", "browser": "edge", "focus": True})]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["app.open_url", "answer.text"]
    assert result["answer"] == "Ссылка открыта, окно найдено, но сфокусировать окно не удалось."


def test_app_open_url_opened_unverified_synthesizes_terminal_answer():
    sent = []
    captured = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        if action != "app.open_url":
            raise AssertionError("terminal partial URL evidence must not execute follow-up tools")
        return {
            "status": "opened_unverified",
            "url": params["url"],
            "launched": True,
            "window_found": False,
            "terminal_sufficient": True,
            "completion_state": "partial_success",
            "recommended_next": "answer_text",
        }

    result = _run_case([
        _message(tool_calls=[_tool_call("call-open", "app_open_url", {"url": "https://irumode.online/"})]),
        _message(tool_calls=[_tool_call("call-window", "window_list", {"visible": True})]),
    ], send_command_fn=_send, captured=captured)

    assert sent == [("app.open_url", {"url": "https://irumode.online/"})]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["app.open_url", "answer.text"]
    assert result["commands"][1]["result"]["answer_type"] == "partial_report"
    assert result["commands"][1]["result"]["basis"] == ["step_1"]
    assert "команда открытия URL выполнена" in result["answer"]
    assert len(captured) == 2


def test_window_list_args_are_sanitized_before_agent_dispatch():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        return {"status": "ok", "windows": []}

    result = _run_case([
        _message(tool_calls=[_tool_call("call-window-list", "window_list", {"visible": True, "process_name": "msedge.exe"})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Окна проверены.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send)

    assert sent == [("window.list", {"include_invisible": False})]
    assert result["commands"][0]["result"]["arg_warnings"] == [
        "mapped arg visible to include_invisible",
        "ignored unknown arg: process_name; use window.find for filtering",
    ]


def test_window_find_process_name_is_preserved_by_arg_validation():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        return {"status": "not_found", "matches": []}

    _run_case([
        _message(tool_calls=[_tool_call("call-window-find", "window_find", {"process_name": "msedge.exe", "visible": True})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Окно не найдено.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send)

    assert sent == [("window.find", {"process_name": "msedge.exe", "visible": True})]


def test_app_open_url_unknown_arg_is_rejected_before_agent_dispatch():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        return {"status": "opened_unverified", "launched": True}

    result = _run_case([
        _message(tool_calls=[_tool_call("call-open", "app_open_url", {"url": "https://irumode.online/", "visible": True})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Аргумент visible не поддерживается для app.open_url.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send)

    assert sent == []
    assert result["commands"][0]["tool_name"] == "app.open_url"
    assert result["commands"][0]["result"]["error"] == "unknown_tool_arguments"
    assert result["commands"][0]["result"]["unknown_args"] == ["visible"]


def test_remember_fact_blocked_without_explicit_memory_intent(monkeypatch):
    def _add_user_fact_should_not_run(**kwargs):
        raise AssertionError("unsolicited remember_fact must not write memory")

    monkeypatch.setattr(controller_non_pipeline.db, "add_user_fact", _add_user_fact_should_not_run)

    result = _run_case([
        _message(tool_calls=[_tool_call("call-memory", "remember_fact", {"text": "browser opened"})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Память не изменял, потому что пользователь не просил ничего запоминать.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], mem_user_id="user-1")

    assert [cmd["tool_name"] for cmd in result["commands"]] == ["remember_fact", "answer.text"]
    assert result["commands"][0]["result"] == {
        "status": "blocked",
        "error": "memory_write_requires_explicit_user_intent",
    }


def test_remember_fact_allowed_with_explicit_memory_intent(monkeypatch):
    saved = []

    monkeypatch.setattr(controller_non_pipeline, "validate_toolchain_fact_against_receipt", lambda text, receipt: (True, text))
    monkeypatch.setattr(
        controller_non_pipeline.db,
        "add_user_fact",
        lambda user_id, text, category=None: saved.append((user_id, text, category)) or 101,
    )

    result = _run_case([
        _message(tool_calls=[_tool_call("call-memory", "remember_fact", {"text": "основной браузер - Comet", "category": "preference"})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Запомнил основной браузер.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], user_message="Запомни, что основной браузер на этом ПК - Comet", mem_user_id="user-1")

    assert saved == [("user-1", "основной браузер - Comet", "preference")]
    assert result["commands"][0]["result"]["status"] == "ok"


def test_duplicate_device_get_passport_is_guarded_then_answer_text():
    calls = []

    async def device_tool_fn(name, args):
        calls.append((name, args["device_id"]))
        return {"status": "ok", "device_id": args["device_id"], "hostname": "devbox"}

    result = _run_case([
        _message(tool_calls=[_tool_call("call-passport-1", "device_get_passport", {})]),
        _message(tool_calls=[_tool_call("call-passport-2", "device_get_passport", {})]),
        _message(tool_calls=[_answer_call(
            "call-answer",
            "Device passport was read.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], device_tool_fn=device_tool_fn)

    assert calls == [("device_get_passport", "device-1")]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["device.get_passport", "answer.text"]


def test_repeat_guard_does_not_block_execute_write_or_window_find():
    sent = []

    async def _send(device_id, action, params):
        sent.append((action, dict(params)))
        if action == "window.find":
            return {"status": "not_found", "matches": []}
        return {"status": "ok", "returncode": 0, "stdout": "ok", "stderr": "", "path": params.get("path")}

    execute_result = _run_case([
        _message(tool_calls=[_tool_call("call-exec-1", "execute_cmd", {"command": "echo ok"})]),
        _message(tool_calls=[_tool_call("call-exec-2", "execute_cmd", {"command": "echo ok"})]),
        _message(tool_calls=[_answer_call("call-answer", "done", answer_type="grounded_report", basis=["step_1", "step_2"])]),
    ], send_command_fn=_send)

    write_result = _run_case([
        _message(tool_calls=[_tool_call("call-write-1", "write_content", {"path": "C:/Temp/a.txt", "content": "a"})]),
        _message(tool_calls=[_tool_call("call-write-2", "write_content", {"path": "C:/Temp/a.txt", "content": "a"})]),
        _message(tool_calls=[_answer_call("call-answer", "done", answer_type="grounded_report", basis=["step_1", "step_2"])]),
    ], send_command_fn=_send)

    window_result = _run_case([
        _message(tool_calls=[_tool_call("call-window-1", "window_find", {"title_contains": "Demo"})]),
        _message(tool_calls=[_tool_call("call-window-2", "window_find", {"title_contains": "Demo"})]),
        _message(tool_calls=[_answer_call("call-answer", "done", answer_type="grounded_report", basis=["step_1", "step_2"])]),
    ], send_command_fn=_send)

    assert [cmd["tool_name"] for cmd in execute_result["commands"]] == ["execute_cmd", "execute_cmd", "answer.text"]
    assert [cmd["tool_name"] for cmd in write_result["commands"]] == ["write_content", "write_content", "answer.text"]
    assert [cmd["tool_name"] for cmd in window_result["commands"]] == ["window.find", "window.find", "answer.text"]
    assert [action for action, _ in sent].count("execute_cmd") == 2
    assert [action for action, _ in sent].count("write_content") == 2
    assert [action for action, _ in sent].count("window.find") == 2


def test_execute_cmd_ok_stdout_is_terminal_sufficient_without_window_find():
    sent = []

    async def _send(device_id, action, params):
        sent.append(action)
        if action == "window.find":
            raise AssertionError("window.find should not be required after execute_cmd OK evidence")
        return {"returncode": 0, "stdout": "OK: open_requested Downloads", "stderr": ""}

    result = _run_case([
        _message(tool_calls=[_execute_call("call-exec", "open downloads")]),
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Downloads"})]),
    ], user_message="Open Downloads", send_command_fn=_send)

    assert sent == ["execute_cmd"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["execute_cmd", "answer.text"]
    assert result["commands"][0]["summary"] == "OK: open_requested Downloads"
    assert result["commands"][1]["result"]["basis"] == ["step_1"]
    assert "OK: open_requested Downloads" in result["answer"]


def test_execute_cmd_no_stdout_rejects_completed_action_claim():
    sent = []
    failure_payload = {
        "message": "Command did not confirm the requested state.",
        "reason": "execute_cmd returned NO.",
        "recoverable": True,
        "suggested_next_action": "Retry with corrected command or ask for clarification.",
        "basis": ["step_1"],
    }

    async def _send(device_id, action, params):
        sent.append(action)
        return {"returncode": 0, "stdout": "NO: destination_missing B", "stderr": ""}

    result = _run_case([
        _message(tool_calls=[_execute_call("call-exec", "copy A B")]),
        _message(tool_calls=[_answer_call("call-bad-answer", "Copied.", answer_type="grounded_report", basis=["step_1"])]),
        _message(tool_calls=[_tool_call("call-failure", "answer_report_failure", failure_payload)]),
    ], user_message="Copy A to B", send_command_fn=_send)

    assert sent == ["execute_cmd"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["execute_cmd", "answer.report_failure"]
    assert result["commands"][0]["status"] == "failed"
    assert result["answer"] == failure_payload["message"]


def test_execute_cmd_error_stdout_rejects_completed_action_claim():
    sent = []
    failure_payload = {
        "message": "Command reported an error.",
        "reason": "execute_cmd returned ERROR.",
        "recoverable": True,
        "suggested_next_action": "Inspect the error and retry if appropriate.",
        "basis": ["step_1"],
    }

    async def _send(device_id, action, params):
        sent.append(action)
        return {"returncode": 0, "stdout": "ERROR: access denied", "stderr": ""}

    result = _run_case([
        _message(tool_calls=[_execute_call("call-exec", "delete file")]),
        _message(tool_calls=[_answer_call("call-bad-answer", "Deleted.", answer_type="grounded_report", basis=["step_1"])]),
        _message(tool_calls=[_tool_call("call-failure", "answer_report_failure", failure_payload)]),
    ], user_message="Delete file", send_command_fn=_send)

    assert sent == ["execute_cmd"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["execute_cmd", "answer.report_failure"]
    assert result["commands"][0]["status"] == "failed"
    assert result["answer"] == failure_payload["message"]


def test_max_iterations_answer_only_repair_returns_grounded_answer():
    captured = []
    sent = []

    async def _send(device_id, action, params):
        sent.append(action)
        return {"status": "not_found", "matches": []}

    result = _run_case([
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Notepad"})]),
        _message(tool_calls=[_answer_call(
            "call-repair-answer",
            "Notepad window was not found.",
            answer_type="grounded_report",
            basis=["step_1"],
        )]),
    ], send_command_fn=_send, captured=captured, max_iterations=1)

    assert sent == ["window.find"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["window.find", "answer.text"]
    assert result["commands"][1]["result"]["basis"] == ["step_1"]
    repair_tools = captured[-1]["tools"]
    assert [tool["function"]["name"] for tool in repair_tools] == ["answer_text"]


def test_max_iterations_repair_rejects_raw_assistant_text():
    captured = []
    sent = []

    async def _send(device_id, action, params):
        sent.append(action)
        return {"status": "not_found", "matches": []}

    result = _run_case([
        _message(tool_calls=[_tool_call("call-window", "window_find", {"title_contains": "Notepad"})]),
        _message("raw repair answer", tool_calls=None, finish_reason="stop"),
    ], send_command_fn=_send, captured=captured, max_iterations=1)

    assert sent == ["window.find"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["window.find", "tool_only_protocol"]
    assert result["commands"][-1]["result"]["repair_reason"].startswith("repair answer invalid")
    assert [tool["function"]["name"] for tool in captured[-1]["tools"]] == ["answer_text"]


def test_max_iterations_repair_can_return_honest_no_evidence_error_report():
    captured = []
    payload = {
        "answer_type": "error_report",
        "text": "I do not have current-run evidence for this request.",
        "basis": [],
        "self_check": {
            "depends_on_current_external_state": False,
            "claims_completed_action": False,
            "has_sufficient_evidence": False,
            "missing_evidence_question": "No tool result exists in the current run.",
        },
    }

    result = _run_case([
        _message(tool_calls=[_tool_call("call-repair-answer", "answer_text", payload)]),
    ], captured=captured, max_iterations=0)

    assert result["answer"] == "I do not have current-run evidence for this request."
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["answer.text"]
    assert result["commands"][0]["result"]["answer_type"] == "error_report"
    assert result["commands"][0]["result"]["basis"] == []
    assert [tool["function"]["name"] for tool in captured[-1]["tools"]] == ["answer_text"]


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


def test_pipeline_worker_max_iterations_runs_answer_only_repair(monkeypatch):
    monkeypatch.setattr(controller_pipeline, "PIPELINE_WORKER_MAX_ITERATIONS", 1)
    final_text = "pipeline final ok"
    responses = [
        _message(json.dumps({
            "goal": "verify task",
            "steps": [{"title": "run check", "instruction": "run check", "device_id": "device-1"}],
        }), tool_calls=None, finish_reason="stop"),
        _message(tool_calls=[_execute_call("call-exec", "echo ok")]),
        _message(tool_calls=[_answer_call("call-repair-step", "step repaired", answer_type="grounded_report", basis=["step_1"])]),
        _message(tool_calls=[_answer_call("call-final", final_text, answer_type="grounded_report", basis=["step_1"])]),
    ]
    captured = []
    finished = []
    step_status = {}
    sent = []

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
        sent.append(action)
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
    assert sent == ["execute_cmd"]
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["execute_cmd", "answer.text", "answer.text"]
    repair_calls = [kwargs for kwargs in captured if [tool["function"]["name"] for tool in (kwargs.get("tools") or [])] == ["answer_text"]]
    assert repair_calls

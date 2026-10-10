import asyncio
import json
import pytest
from server import controller_pipeline as pipeline
from server.controller_trust import enforce_trusted_answer, has_grounded_terminal_answer
from server.pipeline_step_control import completion_matches
from server.run_journal import append_answer_step, append_tool_step, validate_answer_text_payload
from server.tool_completion import synthesize_terminal_answer_payload
from test_controller_pipeline_budget import _shared_context, _execute_call
from test_controller_trust import _run_non_pipeline_case


def evidence(journal, device, result, status="success"):
    append_tool_step(journal, {"action": "execute_cmd", "command": "check", "device_id": device,
        "target_device_id": device, "result": result, "status": status})


def test_grounded_recovered_answer_is_not_replaced_by_old_error():
    journal = []
    evidence(journal, "Second", {"error": "timeout"}, "failed")
    evidence(journal, "Second", {"returncode": 0, "stdout": "OK: steam_process_running"})
    payload = validate_answer_text_payload(synthesize_terminal_answer_payload(journal[-1]), journal)
    append_answer_step(journal, "answer_text", payload, target_device_id="Second")
    assert enforce_trusted_answer(payload["text"], journal) == payload["text"]
    evidence(journal, "Second", {"error": "new failure"}, "failed")
    assert "ошибкой" in enforce_trusted_answer(payload["text"], journal)


def test_unvalidated_success_does_not_hide_failure():
    journal = []
    evidence(journal, "Second", {"error": "not found"}, "failed")
    assert "not found" in enforce_trusted_answer("Готово", journal)


@pytest.mark.parametrize("tool,result,check", [
    ("app_open_url", {"status": "opened_verified", "window_found": True, "url": "https://music.yandex.ru"}, {"tool": "app_open_url", "url": "https://music.yandex.ru"}),
    ("app_launch", {"status": "launched_verified", "window": {"process_name": "steam.exe"}}, {"tool": "app_launch", "process_name": "steam.exe"}),
])
def test_verified_application_completes_only_matching_explicit_check(tool, result, check):
    entry = {"action": tool, "result": result}
    assert completion_matches({"completion_check": check}, entry)
    assert not completion_matches({}, entry)
    assert not completion_matches({"completion_check": {**check, "url": "wrong", "process_name": "wrong"}}, entry)
    assert not completion_matches({"completion_check": check}, {**entry, "result": {**result, "status": "failed"}})


def test_verified_url_worker_stops_without_another_llm_or_verification(monkeypatch):
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a, **kw: None)
    calls, sent = [], []
    async def completion(**kw):
        calls.append(kw)
        assert len(calls) == 1
        return {"choices": [{"message": {"tool_calls": [{"id": "open", "function": {"name": "app_open_url", "arguments": json.dumps({"url": "https://music.yandex.ru"})}}]}}]}
    async def send(device, action, params):
        sent.append((device, action))
        return {"status": "opened_verified", "launched": True, "window_found": True, "url": "https://music.yandex.ru"}
    result = asyncio.run(pipeline.run_pipeline_worker(client=None, cfg={"model": "mock"}, model="mock",
        shared=_shared_context(), overall_goal="Open music", step={"title": "Open music", "instruction": "Open music",
        "completion_check": {"tool": "app_open_url", "url": "https://music.yandex.ru"}}, completed_steps=[],
        chat_history=[], send_command_fn=send, get_file_link_fn=lambda *a: "", machine_guid=None, mem_user_id=None,
        poll_task_id=None, chat_completion_request_fn=completion, worker_tools=[]))
    assert result["status"] == "ok"
    assert len(calls) == len(sent) == 1
    assert result["commands"][-1]["tool_name"] == "answer.text"


def test_non_pipeline_success_on_first_device_allows_explicit_second_action(monkeypatch):
    from server import database
    monkeypatch.setattr(database, "get_device_profile", lambda *a, **kw: None)
    sent = []
    async def send(device, action, params):
        sent.append(device)
        return {"returncode": 0, "stdout": "OK: opened_verified"}
    responses = [{"choices": [{"message": {"tool_calls": [_execute_call(str(i), "open", device)]}}]} for i, device in enumerate(["device-1", "device-2"])]
    responses.append({"choices": [{"message": {"content": ""}}]})
    _run_non_pipeline_case(responses, send_command_fn=send)
    assert sent == ["device-1", "device-2"]


@pytest.mark.parametrize("valid_terminal,fail_second", [(True, False), (False, False), (False, True)])
def test_plan_reports_each_device_despite_missing_summary_tool(monkeypatch, valid_terminal, fail_second):
    phases = []
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: 1)
    monkeypatch.setattr(pipeline.db, "update_step", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, "finish_task", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [])
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    async def completion(**kw):
        phases.append(kw["phase"])
        if kw["phase"] == "pipeline.plan":
            return {"choices": [{"message": {"content": json.dumps({"steps": [{"title": "Open on " + d, "device_id": d} for d in ("a", "b")]})}}]}
        return {"choices": [{"message": {"content": "raw final"}}]}
    async def worker(**kw):
        target = kw["step"]["device_id"]
        journal = []
        if target == "b" and fail_second:
            evidence(journal, target, {"error": "not found"}, "failed")
            return {"status": "error", "answer": "не открыто", "commands": journal}
        if target == "b": evidence(journal, target, {"error": "timeout"}, "failed")
        evidence(journal, target, {"returncode": 0, "stdout": "OK: opened_verified"})
        if valid_terminal:
            append_answer_step(journal, "answer_text", synthesize_terminal_answer_payload(journal[-1]), target_device_id=target)
        return {"status": "ok", "answer": "Открыто", "commands": journal}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", worker)
    result = asyncio.run(pipeline.process_pipeline_subagents(user_message="Open two apps", device_id="a", device_info={"hostname": "A"},
        all_devices={d: {"info": {"hostname": d}} for d in ("a", "b")}, send_command_fn=None, get_file_link_fn=None,
        chat_history=[], load_llm_config_fn=lambda: {"model": "mock"}, pick_model_fn=lambda *a: "mock",
        chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
    assert result["task_receipt"]["answer_source"] == "pipeline_step_report"
    assert "Open on a" in result["answer"] and "Open on b" in result["answer"]
    assert result["answer"].startswith("План выполнен не полностью." if fail_second else "План выполнен.")
    assert not any(x.get("action") == "tool_only_protocol" for x in result["commands"])
    assert phases.count("pipeline.final") == (0 if valid_terminal else 2)


def test_other_device_success_cannot_hide_failed_device():
    journal = []
    evidence(journal, "a", {"error": "not opened"}, "failed")
    evidence(journal, "b", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    append_answer_step(journal, "answer_text", payload, target_device_id="b")
    assert "not opened" in enforce_trusted_answer(payload["text"], journal)


def test_broadcast_recovered_grounded_result_is_success():
    from server.task_runtime import _device_execution_status
    journal = []
    evidence(journal, "a", {"error": "timeout"}, "failed")
    evidence(journal, "a", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    append_answer_step(journal, "answer_text", payload, target_device_id="a")
    assert _device_execution_status({"answer": payload["text"], "commands": journal}) == "ok"


def test_fallback_report_keeps_confirmed_results_from_both_devices():
    from server.tool_completion import synthesize_device_terminal_report
    journal = []
    evidence(journal, "givi", {"returncode": 0, "stdout": "OK: music_opened"})
    evidence(journal, "Second", {"returncode": 0, "stdout": "OK: steam_opened"})
    payload = validate_answer_text_payload(synthesize_device_terminal_report(journal, journal[-1]), journal)
    assert "givi: OK: music_opened" in payload["text"]
    assert "Second: OK: steam_opened" in payload["text"]
    assert len(payload["basis"]) == 2


def test_runtime_keeps_validated_markdown_recovery_answer(monkeypatch):
    from server import task_runtime as rt
    journal = []
    evidence(journal, "a", {"error": "timeout"}, "failed")
    evidence(journal, "a", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    payload["text"] = "**Приложение открыто.**"
    append_answer_step(journal, "answer_text", payload, target_device_id="a")
    rt.devices["1:a"] = {"user_id": 1, "info": {"os": "Linux"}, "ws": object()}
    rt.tasks["report"] = {"results": {}, "status": "running", "modes": {"plan_declined": True}}
    monkeypatch.setattr(rt, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    async def process(**kw): return {"answer": payload["text"], "commands": journal}
    monkeypatch.setattr(rt, "process_nl_command", process)
    asyncio.run(rt.run_nl_task("report", 1, "open", ["1:a"], 1))
    assert rt.tasks["report"]["answer"] == payload["text"]
    assert has_grounded_terminal_answer(rt.tasks["report"]["answer"], journal)


def test_broadcast_runtime_keeps_each_recovered_answer(monkeypatch):
    from server import task_runtime as rt
    for device in ("a", "b"):
        rt.devices["1:" + device] = {"user_id": 1, "info": {"os": "Linux", "hostname": device}, "ws": object()}
    rt.tasks["broadcast-report"] = {"results": {}, "status": "running", "modes": {"plan_declined": True}}
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    async def process(**kw):
        device = kw["device_id"]
        journal = []
        evidence(journal, device, {"error": "old timeout"}, "failed")
        evidence(journal, device, {"returncode": 0, "stdout": "OK: opened_verified"})
        payload = synthesize_terminal_answer_payload(journal[-1])
        append_answer_step(journal, "answer_text", payload, target_device_id=device)
        return {"answer": payload["text"], "commands": journal}
    monkeypatch.setattr(rt, "process_nl_command", process)
    asyncio.run(rt.run_nl_task("broadcast-report", 1, "open", ["1:a", "1:b"], 1))
    task = rt.tasks["broadcast-report"]
    assert task["status"] == "done" and task["overall_status"] == "success"
    assert "[a] ok" in task["answer"] and "[b] ok" in task["answer"]
    assert "old timeout" not in task["answer"]

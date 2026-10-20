import asyncio
import copy
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from server import database as db
from server.controller import _thinking_request_fields
from server.controller_pipeline import run_pipeline_worker, build_pipeline_task_receipt
from server.response_presentation import failure_explanation, saved_step_reports, worker_presentation
from server.worker_reports import build_worker_report
from server.worker_scheduler import init_worker_storage, owned_job, persist_report, restore_task
from server.task_summary import get_last_run_summary
from server.worker_context import source_references
from test_controller_pipeline_budget import _shared_context, _execute_call, _answer_call


def response(call=None, finish="tool_calls"):
    return {"choices": [{"finish_reason": finish, "message": {"content": "", "tool_calls": [call] if call else []}}],
            "usage": {"prompt_tokens": 18916, "completion_tokens": 4096,
                      "completion_tokens_details": {"reasoning_tokens": 4096}}}


def run_worker(responses):
    sent, requests = [], []
    async def complete(**kwargs):
        if "auditor" in kwargs.get("phase", ""):
            return {"choices": [{"message": {"content": '{"valid":true,"reason":"fixture result"}'}}]}
        requests.append(copy.deepcopy({k: v for k, v in kwargs.items() if k in {"phase", "messages", "cfg"}}))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    async def send(device, action, params):
        sent.append((action, params))
        return {"returncode": 0, "stdout": "observation", "stderr": ""}
    async def run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(client, {"model_reasoner": "deepseek-v4-pro", "max_tokens": 4096},
                "deepseek-v4-pro", _shared_context(), "Create the complete requested package",
                {"title": "Prepare package", "instruction": "ten scenarios plus application", "device_id": "device-1"},
                [], [], send, lambda *_: "unused", None, None, None, chat_completion_request_fn=complete, worker_tools=[])
    return asyncio.run(run()), sent, requests


@pytest.mark.parametrize("truncated", [None, _execute_call("truncated", "must never run"),
                                      {"id": "broken", "function": {"name": "execute_cmd", "arguments": '{"command":'}}])
def test_repeated_truncation_dispatches_nothing_and_keeps_metrics(truncated):
    result, sent, requests = run_worker([response(truncated, "length"), response(_execute_call("also-cut", "no"), "length")])
    assert not sent and len(requests) == 2
    assert result["terminal_reason"] == "model_output_truncated"
    assert requests[1]["phase"].endswith(".truncation_recovery")
    assert requests[1]["cfg"]["max_tokens"] == 4096
    assert result["commands"][-1]["result"]["attempts"][1]["reasoning_tokens"] == 4096
    assert _thinking_request_fields({}, "deepseek-v4-pro", phase=requests[1]["phase"]) == {"thinking": {"type": "disabled"}}


def test_recovery_completes_small_operation_without_replaying_prior_action():
    result, sent, requests = run_worker([
        response(_execute_call("first", "observe once")), response(_execute_call("cut", "must not run"), "length"),
        response(_execute_call("next", "next small operation")), response(_answer_call("end", "Prepared content"))])
    assert [p["command"] for _, p in sent] == ["observe once", "next small operation"]
    assert result["status"] == "ok"
    assert len(requests) == 4


def test_unavailable_provider_recovery_does_not_loop_or_dispatch():
    result, sent, requests = run_worker([response(_execute_call("cut", "no"), "length"), RuntimeError("provider rejected request")])
    assert not sent and len(requests) == 2
    assert result['terminal_reason'] == 'model_output_truncated'
    assert result['commands'][-1]['result']['recovery_status'] == 'unavailable'


def test_cancel_between_truncation_and_recovery_stops_without_dispatch(monkeypatch):
    from server import controller_pipeline as pipeline
    checks = []
    def cancelled(_):
        checks.append(True)
        return len(checks) >= 3
    monkeypatch.setattr(pipeline, 'is_task_cancel_requested', cancelled)
    result, sent, requests = run_worker([response(_execute_call("cut", "no"), "length")])
    assert result['status'] == 'cancelled' and not sent and len(requests) == 1


def failed_task(owner, chat):
    text = "Сохранённый отчёт с числами и источниками.\n" * 120
    commands = [
        {"tool_name": "web_search", "step_id": "step_1", "status": "success", "device_id": "pc",
         "result": {"results": [{"title": "source", "url": "https://example.org", "content": "material"}]}},
        {"tool_name": "answer.text", "step_id": "step_2", "status": "terminal", "device_id": "pc",
         "result": {"answer_type": "grounded_report", "text": text, "basis": ["step_1"],
                    "self_check": {"depends_on_current_external_state": True, "claims_completed_action": False,
                                   "has_sufficient_evidence": True, "missing_evidence_question": ""}}},
        {"tool_name": "pipeline_guard", "step_id": "step_3", "status": "blocked",
         "result": {"error": "model_output_truncated"}}]
    steps = [{"idx": 0, "title": "Research", "status": "done", "summary": text},
             {"idx": 1, "title": "Scenarios", "status": "failed", "terminal_reason": "model_output_truncated"},
             {"idx": 2, "title": "Documents", "status": "blocked"}]
    receipt = build_pipeline_task_receipt(task_status="failed", commands=commands, step_results=steps,
                                          recovery_warnings=[], receipt_dicts=[])
    return {"task_id": "source", "user_id": owner, "chat_id": chat, "status": "failed", "kind": "worker",
            "message": "Original package", "device_ids": ["pc"], "commands": commands, "task_receipt": receipt,
            "answer": "Plan incomplete", "tasks": [], "created_at": time.time(), "worker_id": "worker-1"}


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "copy.sqlite")
    db.init_db(); init_worker_storage()
    user = db.create_user("p0")
    chat = db.create_chat(user["id"], "isolated")["id"]
    task = failed_task(user["id"], chat)
    with db.get_db() as c:
        c.execute("INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (task["task_id"], user["id"], chat, "failed", json.dumps(task), time.time(), time.time()))
    persist_report(task)
    return user, chat, task


def test_reason_and_full_report_survive_sqlite_restore(stored):
    user, chat, original = stored
    source = restore_task(owned_job("source", user["id"]))
    assert source["worker_report"]["error_code"] == "model_output_truncated"
    assert source["worker_report"]["goal_completed"] is False
    assert saved_step_reports(source)[0]["text"] == original["commands"][1]["result"]["text"]
    human = worker_presentation(source)["conversational_response"]
    assert "Research" in human and "Scenarios" in human and "Documents" in human and "бюджет" in human
    assert "created_files" not in human and "Подтверждённых созданных файлов" in human
    full = failure_explanation(source, source["worker_report"], include_reports=True)
    assert saved_step_reports(source)[0]["text"] in full


def test_owned_source_can_be_read_in_pages_without_research_or_status_change(stored):
    user, chat, task = stored
    a = get_last_run_summary(user["id"], chat, source_task_id="source", allowed_devices={"pc"})
    b = get_last_run_summary(user["id"], chat, source_task_id="source", report_offset=a["next_offset"], allowed_devices={"pc"})
    assert a["report_text"] + b["report_text"] == saved_step_reports(task)[0]["text"]
    assert a["current_execution_evidence"] is False
    assert owned_job("source", user["id"])["state"] == "failed"
    refs = source_references(user["id"], chat, ["source"], {"pc"})
    assert "Сохранённые" in refs[0]["summary"] and not refs[0]["summary_verified"]


@pytest.mark.parametrize("owner_delta,chat_delta,devices", [(1, 0, {"pc"}), (0, 1, {"pc"}), (0, 0, {"foreign"})])
def test_source_report_ownership_chat_and_device_are_enforced(stored, owner_delta, chat_delta, devices):
    user, chat, task = stored
    result = get_last_run_summary(user["id"] + owner_delta, chat + chat_delta, source_task_id="source", allowed_devices=devices)
    assert result["status"] == "blocked" and "report_text" not in result


def test_report_text_without_accepted_terminal_is_not_recovered():
    task = failed_task(1, 1)
    task["commands"][1]["result"]["basis"] = ["missing"]
    assert not saved_step_reports(task)


def test_legacy_research_action_claim_does_not_hide_accepted_step_report():
    task = failed_task(2, 88)
    task['commands'][1]['result']['self_check']['claims_completed_action'] = True
    text = task['commands'][1]['result']['text']
    assert saved_step_reports(task)[0]['text'] == text
    report = build_worker_report(task)
    assert report['status'] == 'failed' and not report['goal_completed']
    assert text in failure_explanation(task, report, include_reports=True)


@pytest.mark.parametrize("question,view", [("Что случилось?", "summary"), ("Что получилось?", "summary"),
                                           ("Покажи промежуточный отчёт", "saved_report")])
def test_status_and_report_paths_do_not_admit_worker(stored, monkeypatch, question, view):
    from server import orchestrator as orch
    user, chat, task = stored
    async def decide(*args, **kwargs):
        return orch.Decision(intent="task_status", reference="explicit", task_id="source", result_view=view), {}
    monkeypatch.setattr(orch, "decide", decide)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Viewing persisted results must never admit a Worker")
    cmd = SimpleNamespace(message=question, request_id=question, device_id="", modes={}, broadcast=False)
    result = asyncio.run(orch.run_turn(cmd, user, chat, forbidden))
    assert "Research" in result["answer"] and "бюджет" in result["answer"]
    if view == "saved_report":
        assert saved_step_reports(task)[0]["text"] in result["answer"]
    assert not result.get("worker_task_id")
    assert owned_job("source", user["id"])["state"] == "failed"


def test_new_action_and_continuation_use_delegate_not_old_worker(stored, monkeypatch):
    from server import orchestrator as orch
    user, chat, task = stored
    admissions = []
    async def decide(*args, **kwargs):
        return orch.Decision(intent="delegate", objective="Continue documents using saved research",
                             execution_mode="simple", target_device_ids=["pc"], source_task_ids=["source"]), {}
    monkeypatch.setattr(orch, "decide", decide)
    async def delegate(choice, **kwargs):
        admissions.append(choice)
        return {"task_id": "new-continuation", "status": "queued"}
    cmd = SimpleNamespace(message="Продолжи работу", request_id="continue", device_id="pc", modes={}, broadcast=False)
    result = asyncio.run(orch.run_turn(cmd, user, chat, delegate))
    assert result["worker_task_id"] == "new-continuation"
    assert admissions[0].source_task_ids == ["source"]
    assert owned_job("source", user["id"])["state"] == "failed"


def test_open_with_no_file_can_clarify_without_claiming_an_open(stored, monkeypatch):
    from server import orchestrator as orch
    user, chat, task = stored
    async def decide(*args, **kwargs):
        return orch.Decision(intent="clarify", answer="Созданных файлов нет. Показать сохранённый текстовый отчёт?"), {}
    monkeypatch.setattr(orch, "decide", decide)
    async def forbidden(*args, **kwargs):
        raise AssertionError("No verified object to open")
    cmd = SimpleNamespace(message="Открой что есть", request_id="open", device_id="pc", modes={}, broadcast=False)
    result = asyncio.run(orch.run_turn(cmd, user, chat, forbidden))
    assert "Созданных файлов нет" in result["answer"] and not result.get("worker_task_id")


def test_truncated_answer_only_repair_is_not_accepted():
    from server.answer_repair import run_answer_only_repair_turn
    async def complete(**kwargs):
        return response(_answer_call("cut", "must not be accepted"), "length")
    async def run():
        async with httpx.AsyncClient() as client:
            return await run_answer_only_repair_turn(client=client, cfg={}, model="deepseek-v4-pro", messages=[],
                user_request="Write report", completion_scope="report", journal=[], chat_completion_request_fn=complete,
                target_device_id="pc", hostname="pc", iteration=12)
    result = asyncio.run(run())
    assert not result["ok"] and result["reason"] == "model_output_truncated"


def test_plan_propagates_step_reason_without_final_success_rewrite(stored, monkeypatch):
    from server import controller_pipeline as pipeline
    user, chat, source = stored
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs["phase"])
        assert kwargs["phase"] == "pipeline.plan", "Known failed PLAN must not ask a model to invent final success"
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"goal": "package", "steps": [
            {"title": "Research", "instruction": "research", "device_id": "pc"},
            {"title": "Scenarios", "instruction": "write", "device_id": "pc"},
            {"title": "Documents", "instruction": "save", "device_id": "pc"}]})}}]}
    async def worker(**kwargs):
        if kwargs['step_index'] == 0:
            return {"status": "ok", "answer": source['commands'][1]['result']['text'], "commands": copy.deepcopy(source['commands'][:2])}
        return {"status": "error", "answer": "Output truncated", "terminal_reason": "model_output_truncated",
                "commands": [copy.deepcopy(source['commands'][-1])]}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", worker)
    async def forbidden(*args, **kwargs):
        raise AssertionError("No agent operation in this test")
    result = asyncio.run(pipeline.process_pipeline_subagents("Create package", "pc", {"hostname": "pc", "os": "Windows"},
        {"pc": {"info": {"hostname": "pc", "os": "Windows"}}}, forbidden, lambda *_: None,
        user_id=user['id'], chat_id=chat, load_llm_config_fn=lambda: {"model": "mock", "max_tokens": 4096},
        pick_model_fn=lambda *_: "mock", chat_completion_request_fn=complete, worker_tools=[], windows_rules="", linux_rules=""))
    assert result['task_receipt']['terminal_reason'] == 'model_output_truncated'
    assert result['task_receipt']['step_results'][0]['status'] == 'done'
    assert result['task_receipt']['step_results'][2]['status'] == 'blocked'
    assert result['task_receipt']['task_status'] == 'failed'
    assert saved_step_reports(result)[0]['text'] == saved_step_reports(source)[0]['text']


def test_recovery_wire_request_disables_thinking_without_changing_budget():
    from server.controller import _chat_completion_request
    captured = []
    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response(_answer_call("ok", "content")))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await _chat_completion_request(client, {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com", "api_key": "test-only", "max_tokens": 4096, "reasoning_effort": "high"},
                "deepseek-v4-pro", [{"role": "user", "content": "next small operation"}], tools=[],
                phase="pipeline.worker.step_2.iteration.2.truncation_recovery")
    asyncio.run(run())
    assert captured[0]['thinking'] == {'type': 'disabled'}
    assert 'reasoning_effort' not in captured[0] and captured[0]['max_tokens'] == 4096

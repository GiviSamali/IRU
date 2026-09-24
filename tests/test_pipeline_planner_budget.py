import asyncio
import json

import httpx
import pytest

from server import controller, controller_pipeline as pipeline
from test_controller_pipeline_budget import _answer_call


@pytest.mark.parametrize("phase", ["pipeline.plan", "pipeline.plan.retry"])
@pytest.mark.parametrize("via_context", [False, True])
def test_only_planner_disables_reasoning(phase, via_context):
    cfg = {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro", "reasoning_effort": "high"}
    kwargs = {"usage_context": {"phase": phase}} if via_context else {"phase": phase}
    assert controller._thinking_request_fields(cfg, cfg["model_reasoner"], **kwargs) == {
        "thinking": {"type": "disabled"}}
    for other in ("pipeline.worker.step_1.iteration.1", "pipeline.final", "non_pipeline", "autonomous"):
        assert controller._thinking_request_fields(cfg, cfg["model_reasoner"], phase=other) == {
            "thinking": {"type": "enabled"}, "reasoning_effort": "high"}


@pytest.mark.parametrize("first_content", ["", '{"steps":[{"title":"partial"}]}'])
def test_truncated_plan_retried_once_with_actual_non_thinking_http_payload(monkeypatch, first_content):
    requests, workers, created, finished = [], [], [], []
    original = "Создай презентацию, Word и Excel о локальных моделях в папке на рабочем столе"
    plan = {"steps": [{"title": name, "instruction": name} for name in ("Исследование", "PPTX", "DOCX", "XLSX")]}
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if "tools" in body:
            assert len(workers) == 4
            return httpx.Response(200, json={"choices": [{"message": {
                "tool_calls": [_answer_call("final", "Результаты обработаны.")]}}]})
        assert not workers and not created
        assert body["model"] == "deepseek-v4-pro"
        assert body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body
        assert body["max_tokens"] == 4096  # Not the tiny general worker limit.
        if len(requests) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {
                "content": first_content, "reasoning_content": "private reasoning"}}]})
        assert len(requests) == 2
        assert any(message["content"] == original for message in body["messages"])
        assert "ПОЛНЫЙ компактный" in body["messages"][-1]["content"]
        assert "partial" not in json.dumps(body["messages"])
        assert "private reasoning" not in json.dumps(body["messages"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(plan)}}]})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(controller, "record_llm_usage_event", lambda **kw: None)
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: created.append(kw) or 1)
    monkeypatch.setattr(pipeline.db, "update_step", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, "finish_task", lambda *a: finished.append(a[-1]))
    monkeypatch.setattr(pipeline.db, "get_device_profile", lambda *a: None)
    monkeypatch.setattr(pipeline, "build_memory_block", lambda *a: "")
    monkeypatch.setattr(pipeline, "push_tasks_view", lambda *a: None)
    monkeypatch.setattr(pipeline, "collect_tasks", lambda *a: [])
    async def worker(**kwargs):
        workers.append(kwargs["step"]["title"])
        assert kwargs["overall_goal"] == original
        assert kwargs["cfg"]["max_tokens"] == 64
        return {"status": "ok", "answer": "Результат шага", "commands": []}
    monkeypatch.setattr(pipeline, "run_pipeline_worker", worker)
    async def send(*a): pytest.fail("no real commands")
    cfg = {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro", "max_tokens": 64,
           "api_key": "test-key", "base_url": "https://llm.invalid", "answer_auditor_enabled": False}
    result = asyncio.run(pipeline.process_pipeline_subagents(
        user_message=original, device_id="pc", device_info={"os": "Windows"}, all_devices={},
        send_command_fn=send, get_file_link_fn=lambda *a: "", chat_history=[],
        load_llm_config_fn=lambda: cfg, pick_model_fn=controller._pick_model,
        chat_completion_request_fn=controller._chat_completion_request, worker_tools=[], windows_rules="", linux_rules=""))
    assert workers == ["Исследование", "PPTX", "DOCX", "XLSX"]
    assert len(created) == 1 and finished == ["completed"]
    assert result["task_receipt"]["task_status"] == "completed"
    assert len(requests) == 3  # Two planner requests and the existing final summary.


def test_cancel_after_truncation_prevents_retry_and_execution(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "is_task_cancel_requested", lambda *a: bool(calls))
    async def completion(**kwargs):
        calls.append(kwargs["phase"])
        return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    monkeypatch.setattr(pipeline.db, "create_task", lambda **kw: pytest.fail("cancel must not create a task"))
    async def send(*a): pytest.fail("cancel must not execute")
    result = asyncio.run(pipeline.process_pipeline_subagents(
        user_message="create documents", device_id="pc", device_info={"os": "Windows"}, all_devices={},
        send_command_fn=send, get_file_link_fn=lambda *a: "", chat_history=[],
        load_llm_config_fn=lambda: {"model": "mock-model"}, pick_model_fn=lambda *a: "mock-model",
        chat_completion_request_fn=completion, worker_tools=[], windows_rules="", linux_rules=""))
    assert calls == ["pipeline.plan"]
    assert result["task_receipt"]["task_status"] == "cancelled"

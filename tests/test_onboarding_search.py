import asyncio
import copy
import json
import pytest
from server import controller_onboarding as mod


def run(monkeypatch, replies, search_result=None):
    requests, searched = [], []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": replies.pop(0)}]}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(copy.deepcopy(kwargs["json"]))
            return Response()
    async def search(query, limit):
        searched.append((query, limit))
        return search_result or {"results": [{"title": "Weather", "url": "https://example.org", "content": "Tomorrow: rain"}]}
    monkeypatch.setattr(mod.httpx, "AsyncClient", Client)
    monkeypatch.setattr(mod, "run_web_search", search)
    monkeypatch.setattr(mod, "record_llm_usage_event", lambda **kwargs: None)
    result = asyncio.run(mod.process_onboarding_message("Погода в Тейково завтра",
        load_llm_config_fn=lambda: {"base_url": "https://test.invalid", "api_key": "test", "model": "test"},
        current_datetime_msk_fn=lambda: "2026-09-23"))
    return result, requests, searched


def call(name="web_search", arguments=None):
    return {"role": "assistant", "tool_calls": [{"id": "s1", "type": "function",
        "function": {"name": name, "arguments": arguments or json.dumps({"query": "Тейково 24 сентября 2026"})}}]}


def test_no_device_search_executes_and_returns_results_to_model(monkeypatch):
    result, requests, searched = run(monkeypatch, [call(), {"content": "Завтра дождь."}])
    assert searched == [("Тейково 24 сентября 2026", 5)]
    assert result["answer"] == "Завтра дождь."
    assert result["commands"][0]["tool_name"] == "web_search"
    assert [t["function"]["name"] for t in requests[0]["tools"]] == ["web_search"]
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert "Tomorrow: rain" in requests[1]["messages"][-1]["content"]


def test_literal_tool_markup_retries_without_executing_text(monkeypatch):
    result, requests, searched = run(monkeypatch, [{"content": '<tool_call>{"name":"web_search"}</tool_call>'}, call(), {"content": "Дождь"}])
    assert len(requests) == 3 and len(searched) == 1
    assert "tool_call" not in result["answer"]


def test_repeated_markup_is_bounded(monkeypatch):
    result, requests, searched = run(monkeypatch, [{"content": "<tool_call>bad</tool_call>"}] * 4)
    assert len(requests) == 4 and not searched
    assert "tool_call" not in result["answer"]


@pytest.mark.parametrize("message", [call("execute_cmd"), call(arguments="bad"), call(arguments='{"query": 1}'), call(arguments='{"query":"x", "max_results":"bad"}')])
def test_invalid_or_device_tools_never_execute(monkeypatch, message):
    result, requests, searched = run(monkeypatch, [message, {"content": "Ошибка"}])
    assert not searched
    assert "error" in requests[1]["messages"][-1]["content"]


def test_provider_error_is_given_to_model(monkeypatch):
    result, requests, searched = run(monkeypatch, [call(), {"content": "Поиск недоступен"}], {"error": "YANDEX_SEARCH_API_KEY missing"})
    assert "YANDEX_SEARCH_API_KEY missing" in requests[1]["messages"][-1]["content"]
    assert result["commands"][0]["status"] == "failed"


def test_plain_chat_stays_one_call(monkeypatch):
    result, requests, searched = run(monkeypatch, [{"content": "Привет"}])
    assert result["answer"] == "Привет" and len(requests) == 1 and not searched


def test_search_receipt_is_saved_for_ui_and_history(monkeypatch):
    from server import task_runtime as runtime
    from server.runtime_state import tasks
    receipt = {"tool_name": "web_search", "result": {"results": []}}
    async def process(**kwargs):
        return {"answer": "Ничего не найдено", "commands": [receipt]}
    saved = []
    monkeypatch.setattr(runtime, "process_onboarding_message", process)
    monkeypatch.setattr(runtime, "get_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime, "add_message", lambda *args: saved.append(args))
    tasks["search"] = {}
    asyncio.run(runtime.run_onboarding_task("search", 1, "Погода", 1))
    assert tasks["search"]["commands"] == [receipt]
    assert saved[0][-1] == [receipt]

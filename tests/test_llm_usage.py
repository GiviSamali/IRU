import asyncio
import time

import httpx

from server.llm_usage import extract_usage, estimate_deepseek_cost_usd


def _login_headers(client, user: dict) -> dict:
    response = client.post("/api/auth", json={"token": user["token"]})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_extract_usage_normal_and_missing_cache_fields():
    data = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_cache_hit_tokens": 70,
            "prompt_cache_miss_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 3},
        }
    }

    usage = extract_usage(data)

    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 25
    assert usage["total_tokens"] == 125
    assert usage["cache_hit_tokens"] == 70
    assert usage["cache_miss_tokens"] == 30
    assert usage["reasoning_tokens"] == 3

    fallback = extract_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    assert fallback["total_tokens"] == 15
    assert fallback["cache_hit_tokens"] == 0
    assert fallback["cache_miss_tokens"] == 10


def test_estimate_deepseek_cost_flash_cache_hit_miss():
    usage = {
        "cache_hit_tokens": 500_000,
        "cache_miss_tokens": 500_000,
        "completion_tokens": 100_000,
    }

    cost = estimate_deepseek_cost_usd("deepseek-chat", usage)

    assert cost == round((0.5 * 0.0028) + (0.5 * 0.14) + (0.1 * 0.28), 8)


def test_add_llm_usage_event_and_summary_aggregation(client):
    from server.database import add_llm_usage_event, create_user, get_llm_usage_summary

    user = create_user("usage-summary-user")
    add_llm_usage_event(
        user_id=user["id"],
        chat_id=123,
        poll_task_id="poll-1",
        route="non_pipeline",
        phase="non_pipeline.iteration.1",
        model="deepseek-chat",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cache_miss_tokens=100,
        estimated_cost_usd=0.000028,
    )

    summary = get_llm_usage_summary(user["id"], "today")

    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 50
    assert summary["total_tokens"] == 150
    assert summary["llm_calls"] == 1
    assert summary["estimated_cost_usd"] == 0.000028


def test_usage_summary_api_requires_auth_and_isolates_chat_usage(client):
    from server.database import add_llm_usage_event, create_chat, create_user

    user = create_user("usage-api-user")
    other = create_user("usage-api-other")
    headers = _login_headers(client, user)
    user_chat = create_chat(user["id"], "user chat")
    second_user_chat = create_chat(user["id"], "second user chat")
    other_chat = create_chat(other["id"], "other chat")
    add_llm_usage_event(user_id=user["id"], chat_id=user_chat["id"], total_tokens=10, model="deepseek-chat")
    add_llm_usage_event(user_id=user["id"], chat_id=second_user_chat["id"], total_tokens=20, model="deepseek-chat")
    add_llm_usage_event(user_id=other["id"], chat_id=other_chat["id"], total_tokens=999, model="deepseek-chat")

    assert client.get("/api/usage/summary").status_code in {401, 403}

    summary_response = client.get("/api/usage/summary", headers=headers)
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["status"] == "ok"
    assert payload["summary"]["today"]["total_tokens"] == 30
    assert payload["limits"]["enforced"] is False

    chat_response = client.get(f"/api/chats/{user_chat['id']}/usage", headers=headers)
    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    assert chat_payload["summary"]["total_tokens"] == 10
    assert [event["total_tokens"] for event in chat_payload["recent_events"]] == [10]

    other_response = client.get(f"/api/chats/{other_chat['id']}/usage", headers=headers)
    assert other_response.status_code == 404


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self.text = ""
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    async def post(self, url, headers=None, json=None):
        return _FakeResponse({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        })


def test_chat_completion_request_records_usage_and_survives_db_failure(client, monkeypatch):
    from server.controller import _chat_completion_request
    from server.database import create_user, get_llm_usage_summary
    import server.llm_usage as llm_usage_module

    user = create_user("usage-wrapper-user")
    result = asyncio.run(_chat_completion_request(
        client=_FakeClient(),
        cfg={"base_url": "https://api.deepseek.com/v1", "api_key": "secret", "model": "deepseek-chat"},
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hi"}],
        usage_context={"user_id": user["id"], "route": "test", "phase": "unit"},
    ))

    assert result["choices"][0]["message"]["content"] == "ok"
    assert get_llm_usage_summary(user["id"], "today")["total_tokens"] == 16

    monkeypatch.setattr(llm_usage_module.db, "add_llm_usage_event", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    result = asyncio.run(_chat_completion_request(
        client=_FakeClient(),
        cfg={"base_url": "https://api.deepseek.com/v1", "api_key": "secret", "model": "deepseek-chat"},
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hi"}],
        usage_context={"user_id": user["id"], "route": "test", "phase": "unit"},
    ))
    assert result["choices"][0]["message"]["content"] == "ok"


def test_classify_task_complexity_usage_is_attributed(client, monkeypatch):
    import server.controller as controller
    from server.database import create_user, get_llm_usage_summary_for_poll_task

    user = create_user("usage-classify-user")
    monkeypatch.setattr(controller, "load_llm_config", lambda: {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret",
        "model": "deepseek-chat",
    })
    monkeypatch.setattr(controller.httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(controller.classify_task_complexity(
        "hello",
        usage_context={
            "user_id": user["id"],
            "chat_id": 321,
            "poll_task_id": "classify-task-1",
            "route": "classification",
            "phase": "classify_task_complexity",
        },
    ))

    assert result == ("SIMPLE", "")
    summary = get_llm_usage_summary_for_poll_task(user["id"], "classify-task-1")
    assert summary["total_tokens"] == 16


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return _FakeClient()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_no_device_onboarding_llm_usage_is_attributed_to_user(client, monkeypatch):
    import server.controller as controller
    import server.controller_onboarding as controller_onboarding
    from server.database import create_user

    monkeypatch.setattr(controller, "load_llm_config", lambda: {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret",
        "model": "deepseek-chat",
    })
    monkeypatch.setattr(controller_onboarding.httpx, "AsyncClient", _FakeAsyncClient)
    user = create_user("usage-no-device-user")
    headers = _login_headers(client, user)

    start_summary = client.get("/api/usage/summary", headers=headers)
    assert start_summary.status_code == 200
    assert start_summary.json()["summary"]["today"]["total_tokens"] == 0

    response = client.post(
        "/nl_command",
        headers=headers,
        json={"message": "Как подключить первое устройство?", "device_id": ""},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["device_ids"] == []

    task_id = payload["task_id"]
    for _ in range(30):
        task_response = client.get(f"/api/tasks/{task_id}", headers=headers)
        assert task_response.status_code == 200
        if task_response.json()["task"]["status"] != "running":
            break
        time.sleep(0.05)

    task_payload = client.get(f"/api/tasks/{task_id}", headers=headers).json()["task"]
    assert task_payload["status"] == "done", task_payload.get("answer")

    summary = client.get("/api/usage/summary", headers=headers).json()["summary"]["today"]
    assert summary["prompt_tokens"] == 12
    assert summary["completion_tokens"] == 4
    assert summary["total_tokens"] == 16

    chat_usage = client.get(f"/api/chats/{payload['chat_id']}/usage", headers=headers).json()
    assert chat_usage["summary"]["total_tokens"] == 16
    assert chat_usage["recent_events"][0]["route"] == "onboarding"

    task_usage = client.get(f"/api/tasks/{task_id}/usage", headers=headers).json()
    assert task_usage["summary"]["total_tokens"] == 16

import asyncio
from urllib.parse import urlencode

import httpx
import pytest

from server import voice
from server.runtime_state import tasks


def setup_task(status="done", answer="Готово, файл создан."):
    from server.database import create_user
    user = create_user("voice-test")
    tasks["voice-task"] = {"user_id": user["id"], "status": status, "answer": answer,
                           "commands": [{"stdout": "DO NOT SPEAK"}], "tasks": [{"title": "INTERNAL"}]}
    return {"X-Token": user["token"]}


def test_speech_requires_auth(client):
    assert client.get("/api/voice/config").status_code == 401
    assert client.post("/api/voice/tasks/voice-task/speech").status_code == 401


def test_speech_rejects_other_user_and_unknown_task(client):
    headers = setup_task()
    from server.database import create_user
    other = create_user("other-voice-user")
    assert client.post("/api/voice/tasks/voice-task/speech", headers={"X-Token": other["token"]}).status_code == 404
    assert client.post("/api/voice/tasks/missing/speech", headers=headers).status_code == 404


@pytest.mark.parametrize("status", ["running", "confirm", "cancelling"])
def test_no_speech_until_terminal(client, status):
    headers = setup_task(status)
    assert client.post("/api/voice/tasks/voice-task/speech", headers=headers).status_code == 409


def test_only_main_answer_reaches_provider(client, monkeypatch):
    headers = setup_task(answer="Готово. ```powershell\nRemove-Item secret\n``` [Файл](/api/download/secret)")
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    spoken = []
    async def shorten(task):
        assert task["answer"].startswith("Готово.")
        return "Готово. Файл создан."
    monkeypatch.setattr(voice, "shorten_answer", shorten)
    async def synthesize(text):
        spoken.append(text)
        return b"ogg-test"
    monkeypatch.setattr(voice, "synthesize", synthesize)
    response = client.post("/api/voice/tasks/voice-task/speech", headers=headers, json={"text": "INJECTED"})
    assert response.status_code == 200
    assert spoken == ["Готово. Файл создан."]
    assert response.content == b"ogg-test"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-voice-parts"] == "1"


def test_empty_answer_does_not_read_commands(client):
    headers = setup_task(answer="")
    assert client.post("/api/voice/tasks/voice-task/speech", headers=headers).status_code == 204


def test_missing_config_is_explicit_and_no_keys_exposed(client, monkeypatch):
    headers = setup_task()
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    assert client.get("/api/voice/config", headers=headers).json()["available"] is False
    assert client.post("/api/voice/tasks/voice-task/speech", headers=headers).status_code == 503


def test_provider_error_is_sanitized_and_slot_released(client, monkeypatch):
    headers = setup_task()
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    async def fail(text):
        raise RuntimeError("PRIVATE provider response and credential")
    monkeypatch.setattr(voice, "synthesize", fail)
    for _ in range(2):
        response = client.post("/api/voice/tasks/voice-task/speech", headers=headers)
        assert response.status_code == 502
        assert "PRIVATE" not in response.text


def test_long_answer_parts_preserve_text_and_fit_form_limit():
    text = ("Очень длинный ответ. " * 900).strip()
    parts = voice.answer_parts(text)
    assert " ".join(parts) == text
    assert len(parts) > 1
    for part in voice.answer_parts("😀" * 3000):
        assert len(urlencode({"text": part, "voice": "zahar"}).encode()) < 15000


def test_part_index_and_rate_limit(client, monkeypatch):
    headers = setup_task(answer="Ответ. " * 200)
    tasks["voice-task"]["message"] = "Расскажи подробно"
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    spoken = []
    async def synthesize(text):
        spoken.append(text)
        return b"ogg"
    monkeypatch.setattr(voice, "synthesize", synthesize)
    response = client.post("/api/voice/tasks/voice-task/speech?part=1", headers=headers)
    assert response.status_code == 200
    assert spoken == [voice.answer_parts(tasks["voice-task"]["answer"])[1]]
    assert client.post("/api/voice/tasks/voice-task/speech?part=-1", headers=headers).status_code == 422
    assert client.post("/api/voice/tasks/voice-task/speech?part=999", headers=headers).status_code == 404
    from server.routers import voice as router
    monkeypatch.setattr(router, "check_rate_limit", lambda _: False)
    assert client.post("/api/voice/tasks/voice-task/speech", headers=headers).status_code == 429


def test_adapter_keeps_deeptalk_voice_without_local_audio_dependencies(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    observed = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            observed.append((url, kwargs))
            return httpx.Response(200, content=b"ogg", request=httpx.Request("POST", url))
    monkeypatch.setattr(voice.httpx, "AsyncClient", Client)
    assert asyncio.run(voice.synthesize("Готово.")) == b"ogg"
    url, request = observed[0]
    assert url == voice.TTS_URL
    assert request["headers"]["Authorization"] == "Api-Key test-key"
    assert request["data"]["voice"] == "zahar"
    assert request["data"]["speed"] == "1.2"

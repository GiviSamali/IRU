import asyncio
import json

import pytest
from fastapi import HTTPException

from server.api_support import needs_confirmation, is_deletion_command
from server.command_confirmation import command_confirmation
from server import task_runtime as runtime
from server.routers import tasks as routes


@pytest.mark.parametrize("command", [
    "New-Item -ItemType File -Path report.txt",
    "New-Item -ItemType Directory -Path Reports",
    "Set-Content -Path report.txt -Value 'Word document about platform and skill development'",
    "python build.py --title 'Word report'", "touch report.txt", "mkdir reports",
])
def test_creation_and_words_ending_like_delete_aliases_need_no_confirmation(command):
    assert not needs_confirmation(command)
    assert not is_deletion_command(command)


@pytest.mark.parametrize("command", [
    "Remove-Item report.txt", "del report.txt", "rd reports", "rmdir reports", "rm report.txt",
    "Clear-Content report.txt", "New-Item report.txt; Remove-Item helper.py",
    "python -c \"import os; os.remove('report.txt')\"",
    "python -c \"import shutil; shutil.rmtree('reports')\"",
    "python -c \"from pathlib import Path; Path('report.txt').unlink()\"",
    "[System.IO.File]::Delete('report.txt')",
])
def test_deletion_including_compound_creation_still_requires_confirmation(command):
    assert needs_confirmation(command)
    assert is_deletion_command(command)


def test_agent_dispatch_allows_creation_but_holds_deletion(monkeypatch):
    sent = []
    class WS:
        async def send_text(self, message):
            payload = json.loads(message)["payload"]
            sent.append(payload)
            runtime.devices["1:pc"]["pending"].pop(payload["id"]).set_result({"returncode": 0})
    monkeypatch.setitem(runtime.devices, "1:pc", {"info": {"os": "Windows"}, "pending": {}, "ws": WS()})
    monkeypatch.setattr(runtime, "get_device_profile", lambda *a: None)
    async def scenario():
        await runtime.send_command_to_agent("1:pc", "execute_cmd", {"command": "Set-Content report.txt 'Word document'"})
        await runtime.send_command_to_agent("1:pc", "write_content", {"path": "report.txt", "content": "Word document"})
        with pytest.raises(RuntimeError, match="CONFIRM_REQUIRED"):
            await runtime.send_command_to_agent("1:pc", "execute_cmd", {"command": "Remove-Item report.txt"})
        assert len(sent) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("pipeline", [True, False])
@pytest.mark.parametrize("accepted", [True, False])
def test_decision_binds_user_and_command_and_is_one_shot(monkeypatch, pipeline, accepted):
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": request or 1})
    monkeypatch.setattr(routes, "add_message", lambda *a, **kw: None)
    sent = []
    async def send(device, action, params, **kw):
        sent.append((device, params, kw))
        return {"returncode": 0}
    monkeypatch.setattr(routes, "send_command_to_agent", send)
    async def scenario():
        data = command_confirmation({"command": "Remove-Item report.txt", "device_id": "pc",
                                     "params": {"command": "Remove-Item report.txt"}})
        task = {"task_id": "delete", "user_id": 1, "chat_id": 1, "status": "confirm", "confirm_data": data,
                "modes": {"pipeline": pipeline}}
        monkeypatch.setitem(runtime.tasks, "delete", task)
        pending = asyncio.get_running_loop().create_future()
        if pipeline: task["_pipeline_confirm_future"] = pending
        body = routes.CommandDecisionBody(confirmation_id=data["confirmation_id"], accepted=accepted)
        with pytest.raises(HTTPException) as foreign:
            await routes.api_command_decision("delete", body, 2)
        assert foreign.value.status_code == 404
        with pytest.raises(HTTPException) as stale:
            await routes.api_command_decision("delete", routes.CommandDecisionBody(confirmation_id="old", accepted=True), None)
        assert stale.value.status_code == 409
        await routes.api_command_decision("delete", body, None)
        with pytest.raises(HTTPException) as duplicate:
            await routes.api_command_decision("delete", body, None)
        assert duplicate.value.status_code == 409
        await asyncio.sleep(0)
        if pipeline:
            assert pending.done() and pending.result() == accepted
            assert not sent
        else:
            assert len(sent) == int(accepted)
            if accepted: assert sent[0][2]["skip_confirm"] is True
    asyncio.run(scenario())


def test_deletion_speech_uses_pending_command_not_answer_or_summary(client, monkeypatch):
    from server import voice
    from test_voice_plan import prepare
    headers, _, _ = prepare(client, monkeypatch, admin=True)
    data = command_confirmation({"command": "Remove-Item secret-name.txt", "params": {"command": "Remove-Item secret-name.txt"}})
    runtime.tasks["offer"].update(status="confirm", plan_suggestion=None, confirm_data=data, answer="OLD ANSWER")
    monkeypatch.setenv("YANDEX_API_KEY", "test-only")
    spoken = []
    async def synthesize(text): spoken.append(text); return b"ogg"
    async def forbidden(*args): pytest.fail("must not summarize command approval using LLM")
    monkeypatch.setattr(voice, "synthesize", synthesize)
    monkeypatch.setattr(voice, "spoken_parts", forbidden)
    assert client.post("/api/voice/tasks/offer/speech?confirmation=old", headers=headers).status_code == 409
    assert client.post(f"/api/voice/tasks/offer/speech?confirmation={data['confirmation_id']}", headers=headers).status_code == 200
    assert spoken == [data["speech"]]
    assert "удаление" in spoken[0] and "да или нет" in spoken[0]
    assert "secret-name" not in spoken[0] and "OLD ANSWER" not in spoken[0]

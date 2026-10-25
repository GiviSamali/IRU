"""Goal completion is spoken from the owner's frozen WorkerReport, not the empty handoff.

Uses a fake TTS provider: no external LLM, device or audio provider calls.
"""
import asyncio
import hashlib
import json
import time

import pytest
from server import database as db, voice
from server.runtime_state import tasks
from server.worker_scheduler import init_worker_storage
from server.goal_presentation import goal_presentation


def goal(state="success", summary="Я проверил все команды, сцена отвечает."):
    member = {"task_id": "member", "status": state, "required_for_goal": True,
              "report": {"task_id": "member", "status": state,
                         "goal_completed": state == "success", "summary": summary,
                         "artifacts": [], "evidence_refs": ["step_1"]},
              "result": {"result_id": "result:member"} if state == "success" else None}
    return {"goal_id": "goal-1", "goal_completed": state == "success", "tasks": [member]}


@pytest.mark.parametrize("status,expected", [
    ("success", "Готово."),
    ("partial", "Выполнено частично"),
    ("unknown", "не подтверждён"),
    ("failed", "Не удалось"),
    ("cancelled", "отменена"),
])
def test_goal_narration_preserves_authoritative_outcome(status, expected):
    projected = goal_presentation(goal(status))
    assert projected["terminal"]
    assert expected in projected["message"]
    assert projected["spoken_message"]
    assert len(projected["spoken_message"]) <= 420
    if status != "success":
        assert not projected["goal_completed"]


def test_goal_narration_speaks_meaningful_worker_summary_without_path():
    projected = goal_presentation(goal())
    assert "Я проверил все команды, сцена отвечает." in projected["message"]
    assert "Я проверил все команды, сцена отвечает." in projected["spoken_message"]
    long_summary = "C:\\Users\\Demo\\secret.txt: " + "Проверено. " * 60
    projected = goal_presentation(goal(summary=long_summary))
    assert "C:\\Users" in projected["message"]  # Written report remains lossless.
    assert "C:\\Users" not in projected["spoken_message"]


def test_api_speaks_terminal_goal_even_when_orchestrator_handoff_was_empty(client, monkeypatch):
    owner = db.create_user("goal-voice-owner")
    chat_id = db.create_chat(owner["id"], "goal-voice")["id"]
    other = db.create_user("goal-voice-other")
    init_worker_storage()
    now = time.time()
    goal_id, member_id, turn_id = "goal-a", "worker-a", "orchestrator-a"
    reported = {"task_id": member_id, "status": "success", "goal_completed": True,
                "summary": "Я проверил канал управления сценой: команды работают.",
                "artifacts": [], "evidence_refs": ["step_1"]}
    with db.get_db() as c:
        c.execute("INSERT INTO worker_goals VALUES(?,?,?,?,?,?)",
                  (goal_id, owner["id"], chat_id, "Проверь управление сценой", 1, now))
        c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (member_id, owner["id"], chat_id, "success",
                   json.dumps({"goal_id": goal_id, "worker_report": reported}),
                   json.dumps(reported), None, now, now, None))
        c.execute("INSERT INTO worker_goal_tasks VALUES(?,?,?)", (member_id, goal_id, 1))
        frozen = json.dumps({"task_id": member_id})
        c.execute("INSERT INTO worker_results VALUES(?,?,?,?,?,?,?)",
                  ("result:" + member_id, member_id, owner["id"], chat_id,
                   frozen, hashlib.sha256(frozen.encode()).hexdigest(), now))
    tasks[turn_id] = {"task_id": turn_id, "user_id": owner["id"], "chat_id": chat_id,
                      "kind": "orchestrator", "result_first": True, "goal_id": goal_id,
                      "status": "done", "answer": "", "device_ids": [],
                      "message": "Проверь управление сценой", "created_at": now}
    spoken = []
    monkeypatch.setattr(voice, "speech_configured", lambda: True)
    async def fake_tts(text):
        spoken.append(text)
        return b"fake-ogg"
    async def forbidden(*args, **kwargs):
        raise AssertionError("Completion speech must not rerun an LLM")
    monkeypatch.setattr(voice, "synthesize", fake_tts)
    monkeypatch.setattr(voice, "shorten_answer", forbidden)
    headers = {"X-Token": owner["token"]}
    view = client.get("/api/tasks/" + turn_id, headers=headers)
    assert view.status_code == 200
    assert "Я проверил канал" in view.json()["task"]["conversational_response"]
    speech = client.post("/api/voice/tasks/" + turn_id + "/speech", headers=headers)
    assert speech.status_code == 200
    assert len(spoken) == 1 and "Я проверил канал" in spoken[0]
    assert client.post("/api/voice/tasks/" + turn_id + "/speech",
                       headers={"X-Token": other["token"]}).status_code == 404
    with db.get_db() as c:
        c.execute("UPDATE worker_jobs SET state='running' WHERE task_id=?", (member_id,))
    tasks[turn_id]["answer"] = ""
    not_done = client.post("/api/voice/tasks/" + turn_id + "/speech", headers=headers)
    assert not_done.status_code == 409

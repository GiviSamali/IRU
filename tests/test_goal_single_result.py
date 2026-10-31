"""One Goal publishes exactly one user-visible result.

A Goal member owns its journal and report, never a chat message: the single
user-visible answer (and therefore the single spoken line) belongs to the
Orchestrator turn of that Goal.
"""
import asyncio

from server import database as db
from server import orchestrator as orch
from server.worker_scheduler import WorkerScheduler, init_worker_storage


def prepare(tmp_path, monkeypatch, name):
    from server.routers import tasks as task_routes

    monkeypatch.setattr(db, "DB_PATH", tmp_path / name)
    db.init_db()
    init_worker_storage()
    db.create_user("reserved-admin-slot")
    owner = db.create_user("goal-single-result")
    chat = db.create_chat(owner["id"], "одна цель — один итог")["id"]
    scheduler = WorkerScheduler()
    scheduler.closing = False
    monkeypatch.setattr(scheduler, "ensure_runner", lambda owner_id: None)
    # The route module holds the global scheduler; bind this test's instance so
    # a scheduler shut down by another test cannot leak in here.
    monkeypatch.setattr(task_routes, "scheduler", scheduler)
    return owner, chat, scheduler


def test_multi_assignment_goal_creates_no_worker_messages(tmp_path, monkeypatch):
    owner, chat, scheduler = prepare(tmp_path, monkeypatch, "one.sqlite")

    async def scenario():
        template = {"user_id": owner["id"], "chat_id": chat,
                    "message": "Отредактируй project.txt и создай summary.txt", "device_ids": []}
        assignments = [{"key": "edit", "objective": "Изменить project.txt"},
                       {"key": "create", "objective": "Создать summary.txt"}]
        return await scheduler.submit_goal(template, assignments, request_key="one-result")

    admitted = asyncio.run(scenario())
    assert len(admitted["tasks"]) == 2
    with db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0] == 2
        assert [row["message_id"] for row in c.execute("SELECT message_id FROM worker_jobs")] == [None, None]
        assert c.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND role='assistant'",
                         (chat,)).fetchone()[0] == 0


def test_single_assignment_goal_also_publishes_no_message(tmp_path, monkeypatch):
    """Even a one-member Goal keeps its text in the Orchestrator result only."""
    from server.routers import tasks as task_routes

    owner, chat, scheduler = prepare(tmp_path, monkeypatch, "two.sqlite")

    async def scenario():
        return await task_routes.submit_worker(
            owner, chat, "Создай файл", [], {}, request_key="single")

    saved = asyncio.run(scenario())
    assert saved["goal_id"]
    with db.get_db() as c:
        row = c.execute("SELECT message_id FROM worker_jobs WHERE task_id=?",
                        (saved["task_id"],)).fetchone()
        assert row is not None and row["message_id"] is None


def test_goal_projection_is_one_text_for_all_members():
    goal = {"goal_id": "g1", "status": "partial", "terminal": True, "tasks": [
        {"required_for_goal": True, "conversational_response": "Файл project.txt изменён.",
         "report": {"summary": "Файл project.txt изменён."}},
        {"required_for_goal": True, "conversational_response": "Файл summary.txt создан.",
         "report": {"summary": "Файл summary.txt создан."}},
    ]}
    text = orch.present_goal_result(goal)
    assert isinstance(text, str)
    assert text.count("Часть поручения выполнить не удалось.") == 1
    # Worker prose is retained in Operations, not echoed by conversation fallback.
    assert "project.txt" not in text and "summary.txt" not in text
    assert goal['tasks'][0]['report']['summary'] == 'Файл project.txt изменён.'


def test_goal_projection_without_members_keeps_single_fallback():
    goal = {"goal_id": "g2", "status": "success", "terminal": True, "tasks": []}
    text = orch.present_goal_result(goal)
    assert text == "Результат сохранён в подробностях поручения."

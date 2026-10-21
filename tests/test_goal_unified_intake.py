"""All natural-language clients share one semantic intake, including old flags."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import database as db
from server.routers import tasks as routes
from server.worker_scheduler import init_worker_storage


@pytest.mark.parametrize("legacy_flag", [False, True])
def test_legacy_orchestrate_flag_cannot_bypass_semantic_intake(tmp_path, monkeypatch, legacy_flag):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "intake.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("unified-intake")
    chat = db.create_chat(owner["id"], "intake")["id"]
    monkeypatch.setattr(routes, "get_current_user", lambda request: owner)
    monkeypatch.setattr(routes, "check_rate_limit", lambda key: True)
    monkeypatch.setattr(routes, "check_ip_rate_limit", lambda key: True)
    observed = []

    async def turn(cmd, user, chat_id, delegate):
        observed.append((cmd.message, user["id"], chat_id))
        return {"status":"ok", "response_type":"orchestrator"}

    monkeypatch.setattr(routes, "run_turn", turn)
    cmd = routes.NLCommand(message="Discuss possible documents", chat_id=chat, orchestrate=legacy_flag)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    result = asyncio.run(routes.nl_command(cmd, request))
    assert result["response_type"] == "orchestrator"
    assert observed == [(cmd.message, owner["id"], chat)]
    with db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0] == 0


def test_retired_pipeline_mode_is_rejected_before_intake_or_worker(monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id":42})
    cmd = routes.NLCommand(message="old client", modes={"pipeline":True})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes.nl_command(cmd, object()))
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "execution_mode_unsupported"


@pytest.mark.parametrize("record", [None, {"user_id":8,"ws":object()}, {"user_id":7,"ws":None}])
def test_missing_foreign_or_offline_target_cannot_reach_worker_admission(monkeypatch, record):
    monkeypatch.setattr(routes, "devices", {"7:pc":record} if record else {})
    async def forbidden(*args, **kwargs):
        raise AssertionError("Invalid target reached Scheduler")
    monkeypatch.setattr(routes, "scheduler", SimpleNamespace(submit_goal=forbidden))
    with pytest.raises(ValueError, match="device_not_owned_or_unavailable"):
        asyncio.run(routes.submit_worker({"id":7}, 1, "request", ["7:pc"], {}))


def test_assignment_device_scope_cannot_expand_the_goal(tmp_path, monkeypatch):
    from server.worker_scheduler import WorkerScheduler
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "assignment-scope.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("assignment-scope")
    chat = db.create_chat(owner["id"], "scope")["id"]
    async def scenario():
        scheduler = WorkerScheduler()
        with pytest.raises(ValueError, match="device_outside_goal_assignment"):
            await scheduler.submit_goal({"user_id":owner["id"], "chat_id":chat, "message":"Only PC A",
                "device_ids":[f"{owner['id']}:a"]}, [
                    {"key":"x", "objective":"Unexpected PC B", "target_device_ids":[f"{owner['id']}:b"]}], request_key="outside")
        with db.get_db() as c:
            assert c.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM worker_goals").fetchone()[0] == 0
    asyncio.run(scenario())

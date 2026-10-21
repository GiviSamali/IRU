"""A multi-result admission cannot spend quota or persist a partial goal."""
import asyncio

import pytest

from server import database as db
from server.runtime_state import tasks
from server.worker_scheduler import WorkerScheduler, init_worker_storage


@pytest.mark.parametrize("remaining", [0, 1, 2, 3])
def test_daily_limit_is_atomic_and_idempotent_at_batch_boundary(tmp_path, monkeypatch, remaining):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"quota.sqlite")
    db.init_db();init_worker_storage()
    db.create_user("reserved-admin-slot")
    owner=db.create_user("quota-owner")
    db.set_user_plan(owner["id"],"free")
    chat=db.create_chat(owner["id"],"three results")["id"]
    limit=db.PLAN_LIMITS["free"]["max_commands_per_day"]
    for _ in range(limit-remaining):db.increment_daily_commands(owner["id"])
    before=db.check_daily_command_limit(owner["id"])["used"]
    scheduler=WorkerScheduler()
    scheduled=[]
    monkeypatch.setattr(scheduler,"ensure_runner",lambda owner_id:scheduled.append(owner_id))
    async def scenario():
        template={"user_id":owner["id"],"chat_id":chat,"message":"Create three independent files","device_ids":[]}
        assignments=[{"key":str(i),"objective":f"Create file {i}"} for i in range(3)]
        if remaining==3:
            admitted=await scheduler.submit_goal(template,assignments,request_key="quota-batch")
            repeated=await scheduler.submit_goal(template,assignments,request_key="quota-batch")
            assert admitted["goal_id"]==repeated["goal_id"]
            assert [task["task_id"] for task in admitted["tasks"]]==[task["task_id"] for task in repeated["tasks"]]
            return
        with pytest.raises(ValueError,match="daily_command_limit_exceeded"):
            await scheduler.submit_goal(template,assignments,request_key="quota-batch")
    asyncio.run(scenario())
    accepted=remaining==3
    assert db.check_daily_command_limit(owner["id"])["used"]==before+(3 if accepted else 0)
    with db.get_db() as c:
        for table,count in {"worker_goals":1,"worker_goal_tasks":3,"worker_jobs":3,"worker_dependencies":0,
                            "worker_goal_requests":1,"worker_goal_request_tasks":3,"messages":3}.items():
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]==(count if accepted else 0)
    assert len([task for task in tasks.values() if task.get("user_id")==owner["id"]])==(3 if accepted else 0)
    assert scheduled==([owner["id"]] if accepted else [])
    assert not scheduler.runners

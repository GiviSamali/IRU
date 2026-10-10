"""Ten handoffs with factual state, queue, voice, outcomes and failures."""
import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
from server import database as db, orchestrator as orch, voice
from server.runtime_state import tasks, devices
from server.worker_scheduler import WorkerScheduler, owned_job, init_worker_storage
from server.worker_reports import build_worker_report
from server.response_presentation import worker_presentation, worker_spoken_response


@pytest.fixture
def owner(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"natural.sqlite")
    db.init_db();init_worker_storage()
    user=db.create_user("natural")
    user["chat_id"]=db.create_chat(user["id"],"natural")["id"]
    devices[f"{user['id']}:pc"]={"user_id":user["id"],"info":{"hostname":"pc","os":"Windows"},"ws":object()}
    return user


def worker(user,objective):
    return {"task_id":uuid.uuid4().hex,"user_id":user["id"],"chat_id":user["chat_id"],
            "message":objective,"device_ids":[f"{user['id']}:pc"],"status":"running",
            "created_at":time.time(),"modes":{},"kind":"worker"}


def verified(task):
    task.update(status="done",answer="Проверенный результат.",
       commands=[{"tool_name":"write_content","step_id":"step_1","status":"success",
                  "device_id":"pc","result":{"path":r"C:\Users\Demo\Desktop\result.txt","bytes_written":2}}],
       tasks=[],task_receipt={"task_status":"completed","final_verification_status":"verified"})


def test_ten_delegations_voice_queue_completion_failure(owner,monkeypatch):
    gate=asyncio.Event()
    decisions=[]
    executed=[]
    async def decide(message,context,**kwargs):
        n=int(message.rsplit("-",1)[-1])
        decisions.append(n)
        speech="Проверю параметры файла." if n==0 else "Всё сделала." if n==6 else ""
        return orch.Decision(intent="delegate",objective=message,target_device_ids=["pc"],spoken_response=speech),{}
    monkeypatch.setattr(orch,"decide",decide)
    async def execute(task):
        executed.append(task["message"])
        if task["message"]=="task-7":raise RuntimeError("mock failure")
        await gate.wait()
        verified(task)
    scheduler=WorkerScheduler(execute)
    async def delegate(choice,request_key):
        return await scheduler.submit(worker(owner,choice.objective),request_key=request_key)
    async def run():
        responses=[]
        for n in range(6):
            cmd=SimpleNamespace(message=f"task-{n}",request_id=f"task-{n}",device_id="pc",modes={},broadcast=False)
            responses.append(await orch.run_turn(cmd,owner,owner["chat_id"],delegate))
        assert decisions==list(range(6))
        assert [r["worker_status"] for r in responses[:5]]==["running","queued","queued","queued","queued"]
        assert responses[5]["worker_task_id"] is None and "Очередь" in responses[5]["answer"]
        assert all(r["answer"]=="" for r in responses[:5])
        assert await voice.spoken_parts(tasks[responses[0]["task_id"]])==["Проверю параметры файла."]
        assert any(m.get("spokenResponse")=="Проверю параметры файла." for m in db.get_messages(owner["chat_id"]))
        for reply in responses[1:5]:
            assert await voice.spoken_parts(tasks[reply["task_id"]])==[]
            assert tasks[reply["task_id"]]["commands"]==[]
        gate.set();await scheduler.runners[owner["id"]]
        for n in range(6,10):
            cmd=SimpleNamespace(message=f"task-{n}",request_id=f"task-{n}",device_id="pc",modes={},broadcast=False)
            reply=await orch.run_turn(cmd,owner,owner["chat_id"],delegate)
            responses.append(reply)
            assert reply["answer"]=="" and reply["worker_task_id"]
            if n==6:assert await voice.spoken_parts(tasks[reply["task_id"]])==[]
            await scheduler.runners[owner["id"]]
        assert decisions==list(range(10)) and len(executed)==9
        assert owned_job(responses[7]["worker_task_id"],owner["id"])["state"]=="failed"
        for reply in responses:
            if reply["worker_task_id"]:
                assert owned_job(reply["worker_task_id"],owner["id"])["state"] in ("success","failed")
        await scheduler.shutdown()
    asyncio.run(run())


def test_voice_final_outcomes_derive_from_report(owner):
    for status in ("partial","failed","blocked","cancelled","unknown","queued","running"):
        task=worker(owner,status);task["status"]=status
        assert build_worker_report(task)["goal_completed"] is False
        assert "Готово, всё сделала." not in worker_presentation(task)["conversational_response"]
    task=worker(owner,"verified")
    verified(task)
    assert build_worker_report(task)["goal_completed"] is True
    assert worker_spoken_response(task)==worker_presentation(task)["conversational_response"]

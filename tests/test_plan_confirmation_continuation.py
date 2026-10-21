"""Goal Worker confirmation retains its frame; all model/device effects are fake."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from server import database as db, task_runtime as runtime
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.routers import tasks as routes
from server.worker_scheduler import WorkerScheduler, init_worker_storage, owned_job, validate_task_graph


@pytest.mark.parametrize("raw", [None, {}, [], ["prepare", None, "verify"],
    [{"key":"x","objective":""}], [{"key":"x","objective":"create","depends_on":["missing"]}]])
def test_invalid_goal_graph_cannot_silently_become_a_smaller_task(raw):
    with pytest.raises(ValueError):
        validate_task_graph(raw)


def test_orphaned_pipeline_confirmation_never_uses_single_command_completion(monkeypatch):
    monkeypatch.setitem(runtime.tasks,"orphan-plan",{
        "user_id":1,"status":"confirm","modes":{"pipeline":True}})
    monkeypatch.setattr(routes,"get_current_user",lambda request:{"id":1})
    async def send(*args,**kwargs):
        pytest.fail("Retired PLAN must not execute")
    monkeypatch.setattr(routes,"send_command_to_agent",send)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes.api_confirm_task("orphan-plan",None))
    assert caught.value.status_code==410
    assert runtime.tasks["orphan-plan"]["status"]=="confirm"


def tool(name,args,number):
    return {"choices":[{"message":{"tool_calls":[{"id":str(number),"function":{
        "name":name,"arguments":json.dumps(args)}}]}}]}


@pytest.mark.parametrize("choice",["confirm","deny","cancel"])
def test_goal_worker_retains_confirmation_frame_and_independent_queue(client,monkeypatch,choice):
    owner=db.create_user("goal-confirmation")
    db.set_user_plan(owner["id"],"pro")
    chat=db.create_chat(owner["id"],"confirmation")["id"]
    init_worker_storage()
    key=f"{owner['id']}:pc"
    monkeypatch.setitem(runtime.devices,key,{"user_id":owner["id"],"ws":object(),
        "info":{"hostname":"pc","os":"Windows"},"pending":{}})
    monkeypatch.setattr(routes,"get_current_user",lambda request:owner)
    monkeypatch.setattr(runtime,"get_device_profile",lambda *a,**kw:None)
    monkeypatch.setattr(db,"get_device_profile",lambda *a,**kw:None)
    monkeypatch.setattr(runtime,"get_messages",lambda *a,**kw:[])
    monkeypatch.setattr(runtime,"add_message",lambda *a,**kw:None)
    monkeypatch.setattr(runtime,"add_training_record",lambda *a,**kw:None)
    async def probe(**kw): pass
    monkeypatch.setattr(runtime,"_probe_python_toolchain_if_needed",probe)
    commands=["Get-Content C:/Temp/note.txt","Remove-Item C:/Temp/helper.txt","Test-Path C:/Temp/helper.txt"]
    replies=iter([*[tool("execute_cmd",{"command":command},i) for i,command in enumerate(commands)],
        tool("answer_report_failure",{"message":"The remaining deliverable is unverified","reason":"unverified",
            "recoverable":False,"suggested_next_action":"Review the remaining result","basis":[]},"final")])
    dispatched=[]; model_calls=[]; started=[]
    async def completion(**kw):
        model_calls.append(kw)
        return next(replies)
    async def send(device,action,params,**kw):
        assert device==key and action=="execute_cmd"
        if params["command"]==commands[1] and not kw.get("confirmation"):
            raise RuntimeError("CONFIRM_REQUIRED")
        dispatched.append(params["command"])
        return {"returncode":0,"stdout":"note" if params["command"]==commands[0] else "False","stderr":""}
    monkeypatch.setattr(runtime,"send_command_to_agent",send)
    async def process(**kw):
        return await process_non_pipeline_command(user_message=kw["user_message"],device_id=kw["device_id"],
            device_info=kw["device_info"],send_command_fn=kw["send_command_fn"],get_file_link_fn=kw["get_file_link_fn"],
            chat_history=kw["chat_history"],user_id=None,chat_id=None,modes={},poll_task_id=kw["poll_task_id"],
            cfg={"model":"mock-model","answer_auditor_enabled":False},system_msg="system",
            machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=6,
            pick_model_fn=lambda *a:"mock-model",chat_completion_request_fn=completion)
    monkeypatch.setattr(runtime,"process_nl_command",process)

    async def execute(task):
        started.append(task["proposed_objective"])
        if task["proposed_objective"]=="Independent result":
            task.update(status="cancelled",answer="Synthetic independent task reached")
        else:
            await runtime.run_nl_task(task["task_id"],owner["id"],task["message"],task["device_ids"],chat)

    async def scenario():
        scheduler=WorkerScheduler(execute)
        try:
            admitted=await scheduler.submit_goal({"user_id":owner["id"],"chat_id":chat,
                "message":"Read the note, delete the temporary helper and verify deletion; prepare other results",
                "device_ids":[key],"results":{},"modes":{}},[
                    {"key":"main","objective":"Read, delete, verify"},
                    {"key":"dependent","objective":"Use confirmed result","depends_on":["main"]},
                    {"key":"independent","objective":"Independent result"}],request_key="confirm-goal")
            task=admitted["tasks"][0]
            for _ in range(300):
                if task.get("status")=="confirm":break
                await asyncio.sleep(.01)
            assert task["status"]=="confirm" and "_command_confirm_future" in task
            assert dispatched==commands[:1] and started==["Read, delete, verify"]
            assert owned_job(admitted["tasks"][1]["task_id"],owner["id"])["state"]=="waiting_dependency"
            assert owned_job(admitted["tasks"][2]["task_id"],owner["id"])["state"]=="queued"
            nonce=task["confirm_data"]["confirmation_id"]
            if choice=="confirm":
                await routes.api_confirm_task(task["task_id"],None,confirmation_id=nonce)
                with pytest.raises(HTTPException):
                    await routes.api_confirm_task(task["task_id"],None,confirmation_id=nonce)
            elif choice=="deny":await routes.api_deny_task(task["task_id"],None)
            else:await routes.api_cancel_task(task["task_id"],None)
            await asyncio.wait_for(scheduler.runners[owner["id"]],5)
            assert dispatched==(commands if choice=="confirm" else commands[:1])
            assert dispatched.count(commands[0])==1
            assert started==["Read, delete, verify","Independent result"]
            assert len(model_calls)==(4 if choice=="confirm" else 2)
            assert task["worker_report"]["goal_completed"] is False
            assert owned_job(admitted["tasks"][1]["task_id"],owner["id"])["state"]=="waiting_dependency"
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())

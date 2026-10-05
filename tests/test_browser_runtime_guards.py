import asyncio
import time
import pytest

from server import controller as controller
from server import task_runtime as runtime
from server import database as db
from server.runtime_state import devices, tasks


def setup_runtime(client, monkeypatch):
    user=db.create_user("runtime-browser-user")
    db.upsert_device_profile("givi",user["id"],{"os":"Windows","hostname":"work","machine_guid":"guid"})
    key=f"{user['id']}:givi"
    devices[key]={"user_id":user["id"],"info":{"os":"Windows"},"ws":object(),"pending":{}}
    tasks["guard-task"]={"task_id":"guard-task","user_id":user["id"],"status":"running","modes":{},"created_at":time.time()}
    chat=db.create_chat(user["id"])
    async def classify(*a,**kw):return "SIMPLE",""
    monkeypatch.setattr(runtime,"classify_task_complexity",classify)
    return user,key,chat["id"]


@pytest.mark.parametrize("text",["Какие вкладки видешь?","Я хочу узнать, что там открыто","дипсик","Покажешь страничку?","браузер"])
def test_runtime_read_does_not_depend_on_words(client,monkeypatch,text):
    user,key,chat=setup_runtime(client,monkeypatch)
    sent=[]
    import server.browser_bridge as bridge
    async def execute(owner,tid,target,operation,args,**kwargs):
        sent.append((owner,target,operation));return {"status":"success","tabs":[]}
    async def process(**kwargs):
        result=await kwargs["send_command_fn"]("givi","web.tabs",{})
        assert result["status"]=="success"
        return {"answer":"No tabs","commands":[],"tasks":[]}
    monkeypatch.setattr(bridge,"execute_browser_action",execute)
    monkeypatch.setattr(runtime,"process_nl_command",process)
    asyncio.run(runtime.run_nl_task("guard-task",user["id"],text,[key],chat))
    assert sent==[(user["id"],"givi","web.tabs")]


@pytest.mark.parametrize("target",["unknown","999:givi","Second"])
def test_runtime_unknown_or_foreign_target_never_falls_back(client,monkeypatch,target):
    user,key,chat=setup_runtime(client,monkeypatch)
    import server.browser_bridge as bridge
    async def execute(*a,**kw):pytest.fail("No browser dispatch for unauthorized target")
    async def process(**kwargs):
        result=await kwargs["send_command_fn"](target,"web.tabs",{})
        assert result["status"]=="failed"
        return {"answer":"Failed","commands":[],"tasks":[]}
    monkeypatch.setattr(bridge,"execute_browser_action",execute)
    monkeypatch.setattr(runtime,"process_nl_command",process)
    asyncio.run(runtime.run_nl_task("guard-task",user["id"],"Покажи вкладки",[key],chat))


@pytest.mark.parametrize("text",["Сделай это", "Можно продолжать", "Напиши ему", "Другими словами"])
def test_main_model_selects_mutations_without_an_extra_llm(client,monkeypatch,text):
    user,key,chat=setup_runtime(client,monkeypatch)
    async def extra_llm(*a,**kw):pytest.fail("Browser dispatch must not call any intent judge")
    monkeypatch.setattr(controller,"_chat_completion_request",extra_llm)
    import server.browser_bridge as bridge
    sent=[]
    async def execute(*a,**kw):sent.append((a[3],kw["external_action"]));return {"status":"success"}
    async def process(**kwargs):
        params={"tab_id":7,"document_id":"doc","revision":"1","element_id":"field"}
        assert (await kwargs["send_command_fn"]("givi","web.fill",{**params,"text":"hello"}))["status"]=="success"
        assert (await kwargs["send_command_fn"]("givi","web.activate",params))["status"]=="success"
        return {"answer":"done","commands":[],"tasks":[]}
    monkeypatch.setattr(bridge,"execute_browser_action",execute)
    monkeypatch.setattr(runtime,"process_nl_command",process)
    asyncio.run(runtime.run_nl_task("guard-task",user["id"],text,[key],chat))
    assert sent==[("web.fill",False),("web.activate",True)]


@pytest.mark.parametrize("bad",[{"javascript":"evil"},{"device_id":"other"},{"dangerous_effect_confirmed":True}])
def test_model_cannot_supply_runtime_authority(client,monkeypatch,bad):
    user,key,chat=setup_runtime(client,monkeypatch)
    import server.browser_bridge as bridge
    async def execute(*a,**kw):pytest.fail("Invalid model arguments cannot dispatch")
    async def process(**kwargs):
        result=await kwargs["send_command_fn"]("givi","web.activate",{"tab_id":7,"document_id":"doc","revision":"1","element_id":"e",**bad})
        assert result["status"]=="failed"
        return {"answer":"denied","commands":[],"tasks":[]}
    monkeypatch.setattr(bridge,"execute_browser_action",execute)
    monkeypatch.setattr(runtime,"process_nl_command",process)
    asyncio.run(runtime.run_nl_task("guard-task",user["id"],"go",[key],chat))


@pytest.mark.parametrize("accept",[False,True])
def test_risky_browser_action_waits_for_owner_button_and_preserves_cancel(client,monkeypatch,accept):
    user,key,chat=setup_runtime(client,monkeypatch)
    import server.browser_bridge as bridge
    from server.routers import tasks as routes
    from fastapi import HTTPException
    from server.command_confirmation import command_confirmation
    sent=[]
    async def execute(*a,**kw):
        sent.append(kw.get("dangerous_effect_confirmed",False))
        return {"status":"success"} if kw.get("dangerous_effect_confirmed") else {"status":"failed","error":"browser_confirmation_required"}
    async def process(**kw):
        result=await kw["send_command_fn"]("givi","web.activate",{"tab_id":7,"document_id":"doc","revision":"1","element_id":"button"})
        return {"answer":"Done","commands":[],"tasks":[]}
    monkeypatch.setattr(bridge,"execute_browser_action",execute)
    monkeypatch.setattr(runtime,"process_nl_command",process)
    monkeypatch.setattr(routes,"get_current_user",lambda req:{"id":req})
    async def scenario():
        pending=asyncio.create_task(runtime.run_nl_task("guard-task",user["id"],"Скачай установщик",[key],chat))
        try:
            for _ in range(100):
                if tasks["guard-task"]["status"]=="confirm":break
                await asyncio.sleep(0)
            task=tasks["guard-task"]
            assert sent==[False] and task["status"]=="confirm" and not task["confirm_data"]["voice_allowed"]
            body=routes.CommandDecisionBody(confirmation_id=task["confirm_data"]["confirmation_id"],accepted=accept)
            with pytest.raises(HTTPException) as foreign:
                await routes.api_command_decision("guard-task",body,user["id"]+100)
            assert foreign.value.status_code==404
            with pytest.raises(HTTPException) as voice:
                await routes.api_command_decision("guard-task",body.model_copy(update={"via_voice":True}),user["id"])
            assert voice.value.status_code==403
            await routes.api_command_decision("guard-task",body,user["id"])
            await asyncio.wait_for(pending,2)
        finally:
            if not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
    asyncio.run(scenario())
    assert sent==([False,True] if accept else [False])

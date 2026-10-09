"""Task outcome persistence and chat API; fake controller/transport only."""
import asyncio
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import database as db, task_runtime as runtime
from server.routers import chats, tasks as routes


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "history.sqlite")
    db.init_db()
    owner = db.create_user("owner")
    foreign = db.create_user("foreign")
    chat = db.create_chat(owner["id"], "history")
    app = FastAPI()
    app.include_router(chats.router)
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield owner, foreign, chat, client


@pytest.mark.parametrize("status,receipt", [
    ("done", {"task_status":"completed", "goal_completed":True}),
    ("done", {"task_status":"completed_with_recovery", "final_verification_status":"verified"}),
    ("blocked", {"task_status":"partial", "goal_completed":False, "command_outcome":"success"}),
    ("failed", {"task_status":"failed"}),
    ("unknown", {"task_status":"unknown"}),
    ("cancelled", {"task_status":"cancelled"}),
])
def test_task_result_survives_history_reload_and_runtime_cleanup(history, status, receipt):
    owner, foreign, chat, client = history
    task = {"task_id":"finished", "status":status, "task_receipt":receipt, "created_at":time.time()-40,
            "modes":{"pipeline":True}, "tasks":[{"status":status,"steps":[{"status":status}]}],
            "message":"goal", "confirm_data":{"token":"never-save"}, "ws":"never-save"}
    commands = [{"tool_name":"execute_cmd", "result":{"returncode":0}}]
    metadata = db.message_task_metadata(task)
    db.add_message(chat["id"], "assistant", "result", commands, task_metadata=metadata)
    runtime.tasks.clear()
    # Each read uses a new SQLite connection, independent of runtime task TTL.
    for _ in range(2):
        response=client.get(f"/api/chats/{chat['id']}/messages",headers={"X-Token":owner["token"]})
        assert response.status_code==200
        saved=response.json()["messages"][0]
        assert all(saved[key]==value for key,value in metadata.items())
        assert saved["commands"]==commands and saved["taskElapsedMs"]>=40000
        assert "never-save" not in str(saved)
    assert client.get(f"/api/chats/{chat['id']}/messages",headers={"X-Token":foreign["token"]}).status_code==404


def test_old_database_is_migrated_without_inventing_outcome(tmp_path, monkeypatch):
    path=tmp_path/'old.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INTEGER, role TEXT, content TEXT, commands TEXT, created_at REAL)")
        connection.execute("INSERT INTO messages VALUES(1,1,'assistant','Выполнено',NULL,1)")
    monkeypatch.setattr(db,'DB_PATH',path)
    db.init_db();db.init_db()
    message=db.get_messages(1)[0]
    assert message['content']=='Выполнено'
    assert not any(key in message for key in ['taskReceipt','taskStatus','taskMode'])
    with db.get_db() as connection:
        connection.execute("UPDATE messages SET task_metadata='broken JSON' WHERE id=1")
    assert db.get_messages(1)==[message]


@pytest.mark.parametrize("receipt", [
    {"task_status":"completed","goal_completed":True},
    {"task_status":"completed_with_recovery","final_verification_status":"verified"},
    {"task_status":"partial","goal_completed":False},
    {"task_status":"blocked"}, {"task_status":"failed"},
])
def test_real_runtime_finalization_saves_existing_receipt_without_worker_changes(history, monkeypatch, receipt):
    owner, foreign, chat, client=history
    did=f"{owner['id']}:pc";tid='history-run'
    runtime.tasks[tid]={"task_id":tid,"user_id":owner['id'],"chat_id":chat['id'],"message":"goal",
        "device_ids":[did],"status":"running","results":{},"modes":{"pipeline":True},"created_at":time.time()}
    runtime.devices[did]={"user_id":owner['id'],"info":{"hostname":"pc","os":"Windows"},"pending":{}}
    async def process(**kwargs):return {"answer":"result","commands":[],"tasks":[{"status":"done"}],"task_receipt":receipt}
    async def probe(**kwargs):pass
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe)
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**kw:None)
    monkeypatch.setattr(runtime,'add_message',db.add_message)
    monkeypatch.setattr(runtime,'add_training_record',lambda *a,**kw:None)
    monkeypatch.setattr(runtime,'enforce_trusted_answer',lambda answer,commands:answer)
    asyncio.run(runtime.run_nl_task(tid,owner['id'],'goal',[did],chat['id']))
    saved=db.get_messages(chat['id'])[-1]
    assert saved['taskReceipt']==receipt and saved['taskMode']=='plan'
    assert saved['tasks']==[{"status":"done"}] and saved['_taskId']==tid


def test_confirmed_command_partial_result_is_saved_in_history(history, monkeypatch):
    owner, foreign, chat, client=history
    tid='confirm-history';did=f"{owner['id']}:pc"
    runtime.tasks[tid]={"task_id":tid,"user_id":owner['id'],"chat_id":chat['id'],"status":"confirm",
        "message":"multiple actions", "created_at":time.time(), "modes":{},"commands":[],
        "confirm_data":{"command":"safe fake","params":{"command":"safe fake"},"device_id":"pc","chat_id":chat['id']}}
    runtime.devices[did]={"user_id":owner['id'],"info":{"os":"Windows"},"pending":{},"ws":object()}
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    monkeypatch.setattr(routes,'add_message',db.add_message)
    async def send(*args,**kwargs):return {"returncode":0,"stdout":"OK: verified"}
    monkeypatch.setattr(routes,'send_command_to_agent',send)
    async def scenario():
        await routes.api_confirm_task(tid,None)
        for _ in range(30):
            if runtime.tasks[tid]['status']!='running':break
            await asyncio.sleep(0)
    asyncio.run(scenario())
    saved=db.get_messages(chat['id'])[-1]
    assert saved['taskStatus']=='blocked' and saved['taskReceipt']['goal_completed'] is False
    assert saved['taskReceipt']['command_outcome']=='success'


def test_metadata_is_optional_and_cannot_replace_message_identity(history):
    owner, foreign, chat, client=history
    db.add_message(chat['id'],'user','user text',task_metadata={'taskStatus':'done'})
    assert 'taskStatus' not in db.get_messages(chat['id'])[0]
    message=db.add_message(chat['id'],'assistant','answer',task_metadata={'taskStatus':'unknown','id':'foreign','role':'user','token':'secret'})
    assert message['role']=='assistant' and isinstance(message['id'],int)
    assert message['taskStatus']=='unknown' and 'token' not in message


def test_current_error_overrides_stale_presentation_cache(history):
    owner, foreign, chat, client=history
    runtime.tasks['stale-cache']={"task_id":"stale-cache","user_id":owner['id'],"chat_id":chat['id'],
        "status":"error","message":"goal","device_ids":[],"created_at":time.time(),
        "history_metadata":{"taskStatus":"completed","taskMode":"ordinary","taskElapsedMs":1200}}
    response=client.get('/api/tasks/stale-cache',headers={'X-Token':owner['token']})
    assert response.status_code==200 and response.json()['task']['presentation_status']=='error'


def test_ordinary_deny_preserves_cancelled_presentation_without_changing_execution_contract(history, monkeypatch):
    owner, foreign, chat, client=history
    runtime.tasks['deny-history']={"task_id":"deny-history","user_id":owner['id'],"chat_id":chat['id'],
        "status":"confirm","message":"goal","device_ids":[],"created_at":time.time(),
        "confirm_data":{"chat_id":chat['id']},"commands":[]}
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    monkeypatch.setattr(routes,'add_message',db.add_message)
    asyncio.run(routes.api_deny_task('deny-history',None))
    assert runtime.tasks['deny-history']['status']=='done'  # Existing execution contract.
    saved=db.get_messages(chat['id'])[-1]
    response=client.get('/api/tasks/deny-history')
    assert saved['taskStatus']=='cancelled' and response.json()['task']['presentation_status']=='cancelled'

"""Result First contract tests. No LLM, device, or production database access."""
import asyncio
import time

import pytest

from server import database as db
from server.goal_presentation import goal_presentation
from server.runtime_state import tasks
from server.routers import chats as chats_route, tasks as task_route
from server import worker_scheduler as queue


def member(task_id, status, *, report=None, result=None, dependencies=None):
    return {"task_id":task_id, "objective":task_id, "status":status,
            "required_for_goal":True, "report":report or {},
            "result":result, "dependencies":dependencies or []}


def good(task_id, filename=None):
    artifact = [{"path":filename,"name":filename.split("\\")[-1],"device_id":"pc",
                 "verified":True}] if filename else []
    return member(task_id,"success",result={"sha256":"abc"},
                  report={"status":"success","goal_completed":True,
                          "evidence_refs":["verified-step"],"artifacts":artifact})


def test_success_uses_verified_final_state_not_previous_write_error():
    goal={"goal_id":"g", "goal_completed":True, "tasks":[
        good("pptx",r"C:\\Desktop\\RAG.pptx"),
        good("xlsx",r"C:\\Desktop\\RAG.xlsx")]}
    result=goal_presentation(goal)
    assert result["status"]=="success" and result["terminal"]
    assert result["completed_count"]==2
    assert "RAG.xlsx" in result["message"]
    assert "write_chunk_too_large" not in result["message"]
    assert len(result["artifacts"])==2


def test_helper_generator_remains_internal_when_final_file_is_verified():
    finished = good("excel")
    finished["expected_artifacts"] = ["RAG.xlsx"]
    finished["report"]["artifacts"] = [
        {"path":r"C:\\AppData\\scripts\\helpers\\make_rag_compare_xlsx.py",
         "name":"make_rag_compare_xlsx.py","verified":True,"device_id":"pc"},
        {"path":r"C:\\Desktop\\RAG.xlsx",
         "name":"RAG.xlsx","verified":True,"device_id":"pc"}]
    response = goal_presentation({"goal_id":"g", "goal_completed":True,"tasks":[finished]})
    assert response["status"]=="success"
    assert "RAG.xlsx" in response["message"]
    assert "make_rag_compare_xlsx.py" not in response["message"]
    assert len(response["artifacts"]) == 2


def test_missing_proof_cannot_be_upgraded_to_success():
    goal={"goal_id":"g","goal_completed":False,"tasks":[
        member("bad","success",report={"goal_completed":True},result=None)]}
    result=goal_presentation(goal)
    assert result["status"]!="success"
    assert result["completed_count"]==0


def test_failed_source_does_not_leave_goal_stuck_forever():
    goal={"goal_id":"g","goal_completed":False,"tasks":[
        member("source","failed"),
        member("pptx","waiting_dependency",
               dependencies=[{"source_task_id":"source","result_id":None}])]}
    result=goal_presentation(goal)
    assert result["terminal"]
    assert result["status"]=="failed"


def test_partial_and_unknown_are_never_complete():
    partial=goal_presentation({"goal_id":"g","goal_completed":False,"tasks":[good("a"),member("b","failed")]})
    assert partial["terminal"] and partial["status"]=="partial"
    assert partial["completed_count"]==1
    unknown=goal_presentation({"goal_id":"g","goal_completed":False,"tasks":[good("a"),member("b","unknown")]})
    assert unknown["status"]=="unknown" and not unknown["goal_completed"]


def test_running_goal_needs_one_aggregate_progress_reply():
    result=goal_presentation({"goal_id":"g","goal_completed":False,"tasks":[
        good("a"),member("b","waiting_confirmation")]})
    assert result["status"]=="running" and not result["terminal"]
    assert result["requires_user_action"] and "Операциях" in result["message"]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"result-first.sqlite")
    db.init_db()
    queue.init_worker_storage()
    user=db.create_user("result-first-user")
    user["chat_id"]=db.create_chat(user["id"],"result first")["id"]
    monkeypatch.setattr(chats_route,"get_current_user",lambda request:user)
    monkeypatch.setattr(task_route,"get_current_user",lambda request:user)
    yield user
    for key, value in list(tasks.items()):
        if value.get("user_id")==user["id"]:
            del tasks[key]


def test_history_hides_only_new_internal_workers(owner,monkeypatch):
    chat=owner["chat_id"]
    orch={"task_id":"orch", "kind":"orchestrator","goal_id":"g",
          "result_first":True,"status":"done"}
    worker={"task_id":"internal","kind":"worker","goal_id":"g",
            "result_first":True,"status":"success"}
    db.add_message(chat,"user","Make files")
    db.add_message(chat,"assistant","",task_metadata=db.message_task_metadata(orch,"orch"))
    db.add_message(chat,"assistant","Internal Worker prose",task_metadata=db.message_task_metadata(worker,"internal"))
    db.add_message(chat,"assistant","Legacy Worker prose",task_metadata={"taskKind":"worker"})
    monkeypatch.setattr(queue,"goal_view",lambda gid,uid,cid:{
        "goal_id":gid,"goal_completed":True,"tasks":[good("a",r"C:\\Desktop\\final.pptx")]})
    result=asyncio.run(chats_route.api_get_messages(chat,object()))["messages"]
    texts=[r["content"] for r in result]
    assert "Internal Worker prose" not in texts
    assert "Legacy Worker prose" in texts
    assert any("final.pptx" in r["content"] and r.get("taskKind")=="goal" for r in result)


def test_orchestrator_task_polled_until_whole_goal_finishes(owner,monkeypatch):
    tid="orchestrator-goal"
    tasks[tid]={"task_id":tid,"user_id":owner["id"],"chat_id":owner["chat_id"],
                "message":"Make two documents","device_ids":[],"kind":"orchestrator",
                "goal_id":"g","result_first":True,"status":"done","answer":"",
                "created_at":time.time(),"commands":[]}
    snapshot={"goal_id":"g","goal_completed":False,
              "tasks":[good("a"),member("b","running")]}
    monkeypatch.setattr(queue,"goal_view",lambda goal_id,uid,cid:snapshot)
    first=asyncio.run(task_route.api_get_task(tid,object()))["task"]
    assert first["status"]=="running" and first["kind"]=="goal"
    snapshot["goal_completed"]=True
    snapshot["tasks"][1]=good("b")
    final=asyncio.run(task_route.api_get_task(tid,object()))["task"]
    assert final["status"]=="done" and final["presentation_status"]=="success"
    assert final["conversational_response"].startswith("Готово.")


def test_goal_cancel_does_not_touch_other_goal(owner,monkeypatch):
    current={"goal_id":"g","goal_completed":False,"tasks":[member("a","running")]}
    monkeypatch.setattr(queue,"goal_view",lambda gid,uid,cid:current if gid=="g" else None)
    calls=[]
    async def cancel(uid,cid,*,scope,goal_id):
        calls.append((uid,cid,scope,goal_id))
        return {"count":1,"cancelled":1,"unknown":0}
    monkeypatch.setattr(queue.scheduler,"cancel_group",cancel)
    result=asyncio.run(task_route.api_cancel_goal("g",owner["chat_id"],object()))
    assert result["cancelled"]==1
    assert calls==[(owner["id"],owner["chat_id"],"goal","g")]

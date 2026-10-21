"""Routing sees assigned deliverables/revisions, not identical copies of the goal."""
import asyncio
import json

from server import database as db, orchestrator as orch
from server.worker_scheduler import WorkerScheduler, init_worker_storage


def seed_job(connection, owner, chat, task_id, state, created):
    connection.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (
        task_id, owner, chat, state, json.dumps({"task_id":task_id,"user_id":owner,"chat_id":chat,
            "message":task_id,"device_ids":[],"status":state}), None, None, created, created, None))


def test_context_distinguishes_sibling_deliverables_and_latest_amendment(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "routing-context.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("routing-context")
    chat = db.create_chat(owner["id"], "documents")["id"]
    scheduler = WorkerScheduler()
    monkeypatch.setattr(scheduler, "ensure_runner", lambda owner_id: None)
    original = "Research and prepare Word and slides"
    admitted = asyncio.run(scheduler.submit_goal({"user_id":owner["id"], "chat_id":chat,
        "message":original, "device_ids":[]}, [
            {"key":"research", "objective":"Preserve research"},
            {"key":"word", "objective":"Create Word document", "depends_on":["research"]},
            {"key":"slides", "objective":"Create slides", "depends_on":["research"]}], request_key="context"))
    context = orch.context_for(owner["id"], chat, "Shorten the Word document", "")
    by_id = {item["task_id"]:item for item in context["tasks"]}
    assert [by_id[task["task_id"]]["objective"] for task in admitted["tasks"]] == [
        "Preserve research", "Create Word document", "Create slides"]
    word = admitted["tasks"][1]
    scheduler.amend_pending(word["task_id"], owner["id"], chat, expected_revision=1,
        human_request="Use a shorter Word document", objective="Create a shorter Word document")
    context = orch.context_for(owner["id"], chat, "What is waiting?", "")
    by_id = {item["task_id"]:item for item in context["tasks"]}
    assert by_id[word["task_id"]]["objective"] == "Create a shorter Word document"
    assert by_id[word["task_id"]]["task_revision"] == 2
    assert all(item["goal_revision"] == 2 for item in by_id.values())
    assert all(item["goal_id"] == admitted["goal_id"] for item in by_id.values())
    with db.get_db() as c:
        # Stale stored success must not become authority for a failed job.
        stale = {"task_id":word["task_id"], "status":"success", "summary":"Created"}
        c.execute("UPDATE worker_jobs SET state='failed',report=? WHERE task_id=?", (json.dumps(stale), word["task_id"]))
    context = orch.context_for(owner["id"], chat, "Was Word created?", "")
    record = next(item for item in context["tasks"] if item["task_id"] == word["task_id"])
    assert record["status"] == "failed" and record["report"] is None


def test_recent_terminal_history_does_not_displace_active_and_waiting_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"active-context.sqlite")
    db.init_db();init_worker_storage()
    owner=db.create_user("active-context")
    chat=db.create_chat(owner["id"],"current work")["id"]
    with db.get_db() as c:
        seed_job(c,owner["id"],chat,"old-active","running",1)
        seed_job(c,owner["id"],chat,"old-waiting","waiting_dependency",2)
        for i in range(20):seed_job(c,owner["id"],chat,f"recent-{i}","cancelled",10+i)
    context=orch.context_for(owner["id"],chat,"What is active?","")
    assert len(context["tasks"])==8
    assert [row["task_id"] for row in context["tasks"][:2]]==["old-active","old-waiting"]
    assert context["tasks"][2]["task_id"]=="recent-19"


def test_truncated_context_cannot_make_two_active_jobs_look_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"ambiguous-context.sqlite")
    db.init_db();init_worker_storage()
    owner=db.create_user("ambiguous-context")
    chat=db.create_chat(owner["id"],"parallel work")["id"]
    with db.get_db() as c:
        c.execute("INSERT INTO worker_owner_capacity VALUES(?,?)",(owner["id"],2))
        seed_job(c,owner["id"],chat,"first-active","running",1)
        seed_job(c,owner["id"],chat,"second-active","running",2)
    monkeypatch.setattr(orch,"context_for",lambda *a,**kw:{"tasks":[{"task_id":"first-active","status":"running"}],
        "devices":[],"history":[],"facts":[],"context_truncated":True})
    async def decide(*args,**kwargs):
        return orch.Decision(intent="cancel",reference="active",task_id="first-active"),{}
    monkeypatch.setattr(orch,"decide",decide)
    async def forbidden(*args,**kwargs):raise AssertionError("Ambiguous cancellation must not admit work")
    from types import SimpleNamespace
    command=SimpleNamespace(message="Cancel the active task",request_id="ambiguous",device_id="",modes={},broadcast=False)
    response=asyncio.run(orch.run_turn(command,owner,chat,forbidden))
    assert "Уточните" in response["answer"]
    with db.get_db() as c:
        assert {row[0] for row in c.execute("SELECT state FROM worker_jobs")}=={"running"}

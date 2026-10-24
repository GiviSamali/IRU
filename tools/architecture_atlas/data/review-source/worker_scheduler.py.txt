"""One bounded, durable FIFO Worker slot per owner in the existing async server."""
import asyncio
import json
import time
import sys
import logging
import sqlite3

try:
    from . import database as db
    from .runtime_state import tasks, mark_task_cancelled, request_task_cancel
    from .api_support import ADMIN_USER_ID
    from .worker_reports import build_worker_report, TERMINAL
    from .response_presentation import worker_presentation
except ImportError:
    import database as db
    from runtime_state import tasks, mark_task_cancelled, request_task_cancel
    from api_support import ADMIN_USER_ID
    from worker_reports import build_worker_report, TERMINAL
    from response_presentation import worker_presentation

MAX_QUEUED = 4
ACTIVE_STATES = ("running", "waiting_confirmation")


def init_worker_storage():
    with db.get_db() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS worker_jobs (
            task_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
            state TEXT NOT NULL, payload TEXT NOT NULL, report TEXT, message_id INTEGER,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, request_key TEXT,
            UNIQUE(owner_user_id, request_key))""")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS worker_one_active_owner ON worker_jobs(owner_user_id) WHERE state IN ('running','waiting_confirmation')")
        connection.execute("CREATE INDEX IF NOT EXISTS worker_owner_recent ON worker_jobs(owner_user_id,created_at DESC)")
        connection.execute("""CREATE INDEX IF NOT EXISTS worker_owner_queue ON worker_jobs(owner_user_id,
            CASE WHEN state IN ('running','waiting_confirmation') THEN 0 WHEN state='queued' THEN 1 ELSE 2 END,
            CASE WHEN state='queued' THEN created_at ELSE -created_at END)""")


def owned_job(task_id, user_id):
    init_worker_storage()
    with db.get_db() as c:
        row = c.execute("SELECT * FROM worker_jobs WHERE task_id=? AND owner_user_id=?", (task_id,user_id)).fetchone()
        return dict(row) if row else None


def list_jobs(user_id, limit=8, *, queue_first=False):
    init_worker_storage()
    ordering = ("CASE WHEN state IN ('running','waiting_confirmation') THEN 0 WHEN state='queued' THEN 1 ELSE 2 END, "
                "CASE WHEN state='queued' THEN created_at ELSE -created_at END, rowid") if queue_first else "created_at DESC"
    with db.get_db() as c:
        return [dict(row) for row in c.execute(
            "SELECT * FROM worker_jobs WHERE owner_user_id=? ORDER BY " + ordering + " LIMIT ?",
            (user_id,min(max(int(limit),1),50)))]


def restore_task(job):
    payload = json.loads(job["payload"])
    snapshot_report=payload.get("worker_report")
    report=(snapshot_report if isinstance(snapshot_report,dict) and snapshot_report.get("status")==job["state"]
            and snapshot_report.get("task_id")==job["task_id"] else json.loads(job["report"]) if job.get("report") else None)
    if report:
        final_snapshot=payload.get("worker_report")==report and isinstance(payload.get("answer"),str)
        payload.update(status=report["status"], worker_report=report,
            answer=payload.get("answer") or report["summary"], commands=payload.get("commands") or [],
            tasks=payload.get("tasks") or [], worker_id="worker-1")
        if final_snapshot:
            return payload
        # Legacy jobs restore evidence from the same persisted message, never another user/chat.
        with db.get_db() as c:
            row=c.execute("SELECT content,commands,task_metadata FROM messages WHERE id=? AND chat_id=?",(job.get("message_id"),job["chat_id"])).fetchone()
        if row:
            payload["answer"]=row["content"];payload["commands"]=json.loads(row["commands"] or "[]")
            metadata=json.loads(row["task_metadata"] or "{}")
            payload["tasks"]=metadata.get("tasks") or [];payload["task_receipt"]=metadata.get("taskReceipt")
            payload["history_metadata"]=metadata
            payload["diagnostic_trace"]=metadata.get("diagnosticTrace") or []
            payload["worker_started_at"]=metadata.get("workerStartedAt")
            if metadata.get("conversationalResponse") is not None:
                payload["conversational_response"]=metadata["conversationalResponse"]
                payload["execution_details"]=metadata.get("executionDetails") or ""
                payload["answer"]=payload["execution_details"] or row["content"]
    return payload


def _persistence_failure(task, event, exc):
    # Protected metadata only; preserve execution status and evidence.
    logging.getLogger("iru.worker").warning("%s task_id=%s type=%s",event,task["task_id"],type(exc).__name__)
    trace=task.setdefault("diagnostic_trace",[])
    if isinstance(trace,list) and len(trace)<256:
        trace.append({"task_id":task["task_id"],"event":event,"error_type":type(exc).__name__})


def _final_snapshot(task, report, previous):
    snapshot=dict(previous)
    # Never serialize transient controller locks/events or transport objects.
    for key in ("answer","commands","tasks","task_receipt","diagnostic_trace","worker_started_at",
                "conversational_response","execution_details","cancel_requested","worker_error_code",
                "history_message_id","history_metadata"):
        if key in task:snapshot[key]=task[key]
    snapshot.update(status=report["status"],worker_report=report)
    return snapshot


def persist_report(task, *, include_report_column=True):
    report=build_worker_report(task);task["worker_report"]=report
    task.update(worker_presentation(task,report))
    metadata=db.message_task_metadata({**task,"status":report["status"],"worker_report":report},task_id=task["task_id"])
    task["history_metadata"]=metadata
    message_id=task.get("history_message_id")
    with db.get_db() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT payload FROM worker_jobs WHERE task_id=? AND owner_user_id=?",(task["task_id"],task["user_id"])).fetchone()
        if row is None:raise ValueError("worker_job_identity_mismatch")
        c.execute("SAVEPOINT worker_history")
        try:
            chat=c.execute("SELECT 1 FROM chats WHERE id=? AND user_id=?",(task["chat_id"],task["user_id"])).fetchone()
            if chat is None:raise ValueError("chat_not_owned_or_deleted")
            if message_id:
                updated=c.execute("UPDATE messages SET content=?,commands=?,task_metadata=? WHERE id=? AND chat_id=? AND role='assistant'",
                    (task["conversational_response"],json.dumps(task.get("commands") or [],ensure_ascii=False),json.dumps(metadata,ensure_ascii=False),message_id,task["chat_id"]))
                if updated.rowcount!=1:
                    # A deleted row can be rebuilt only in the still-owned chat.
                    if c.execute("SELECT 1 FROM messages WHERE id=?",(message_id,)).fetchone():
                        raise ValueError("message_identity_mismatch")
                    message_id=None
            if not message_id:
                saved=c.execute("INSERT INTO messages(chat_id,role,content,commands,task_metadata,created_at) VALUES(?,?,?,?,?,?)",
                    (task["chat_id"],"assistant",task["conversational_response"],json.dumps(task.get("commands") or [],ensure_ascii=False),json.dumps(metadata,ensure_ascii=False),time.time()))
                message_id=saved.lastrowid
                c.execute("UPDATE chats SET updated_at=? WHERE id=? AND user_id=?",(time.time(),task["chat_id"],task["user_id"]))
        except (sqlite3.Error, ValueError) as exc:
            c.execute("ROLLBACK TO worker_history")
            _persistence_failure(task,"history_persistence_failed",exc)
            message_id=None
        finally:
            c.execute("RELEASE worker_history")
        # History may be unavailable; the job still owns the durable result and slot.
        snapshot=_final_snapshot(task,report,json.loads(row["payload"]))
        snapshot["history_message_id"]=message_id
        if include_report_column:
            c.execute("UPDATE worker_jobs SET state=?,report=?,payload=?,message_id=?,updated_at=? WHERE task_id=? AND owner_user_id=?",
                (report["status"],json.dumps(report,ensure_ascii=False),json.dumps(snapshot,ensure_ascii=False),message_id,time.time(),task["task_id"],task["user_id"]))
        else:
            c.execute("UPDATE worker_jobs SET state=?,payload=?,message_id=?,updated_at=? WHERE task_id=? AND owner_user_id=?",
                (report["status"],json.dumps(snapshot,ensure_ascii=False),message_id,time.time(),task["task_id"],task["user_id"]))
    # Do not publish an uncommitted replacement ID if the job update rolls back.
    task["history_message_id"]=message_id
    return report


def _finalize_report(task):
    try:
        return persist_report(task)
    except Exception as exc:
        _persistence_failure(task,"report_persistence_failed",exc)
        if isinstance(exc,sqlite3.Error):
            # The first transaction rolled back. Retry the same snapshot/history
            # without the optional report column; the payload retains its report.
            return persist_report(task,include_report_column=False)
        # Retain the normal evidence-derived outcome in the existing job payload.
        # Avoid the failed report/history projection; do not replay execution.
        report=build_worker_report(task)
        task["worker_report"]=report
        with db.get_db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT payload FROM worker_jobs WHERE task_id=? AND owner_user_id=?",(task["task_id"],task["user_id"])).fetchone()
            if row is None:raise ValueError("worker_job_identity_mismatch")
            snapshot=_final_snapshot(task,report,json.loads(row["payload"]))
            c.execute("UPDATE worker_jobs SET state=?,payload=?,updated_at=? WHERE task_id=? AND owner_user_id=?",
                (report["status"],json.dumps(snapshot,ensure_ascii=False),time.time(),task["task_id"],task["user_id"]))
        return report


class WorkerScheduler:
    def __init__(self, execute=None):
        self.execute=execute;self.runners={};self.closing=False;self.instance_lock=None

    async def submit(self, task, *, request_key=None):
        if self.closing:raise ValueError("worker_shutting_down")
        init_worker_storage()
        owner=task["user_id"]
        if not db.get_chat(task["chat_id"],owner):raise ValueError("chat_not_owned")
        # No await in the SQLite transaction. A partial unique index also prevents
        # competing HTTP requests from claiming the same owner's active slot.
        with db.get_db() as c:
            c.execute("BEGIN IMMEDIATE")
            if request_key:
                old=c.execute("SELECT * FROM worker_jobs WHERE owner_user_id=? AND request_key=?",(owner,request_key)).fetchone()
                if old:
                    previous=json.loads(old["payload"])
                    if any(previous.get(key)!=task.get(key) for key in ("chat_id","message","device_ids","modes","original_request","proposed_objective","orchestrator_execution_mode","source_task_ids")):raise ValueError("request_id_conflict")
                    return tasks.get(old["task_id"]) or restore_task(dict(old))
            active=c.execute("SELECT task_id FROM worker_jobs WHERE owner_user_id=? AND state IN ('running','waiting_confirmation')",(owner,)).fetchone()
            waiting=c.execute("SELECT COUNT(*) FROM worker_jobs WHERE owner_user_id=? AND state='queued'",(owner,)).fetchone()[0]
            if active and waiting >= MAX_QUEUED: raise ValueError("worker_queue_full")
            if owner != ADMIN_USER_ID:
                db.reserve_daily_worker_command(c, owner)
            metadata=db.message_task_metadata({**task,"status":"queued" if active else "running"},task_id=task["task_id"])
            cursor=c.execute("INSERT INTO messages(chat_id,role,content,task_metadata,created_at) VALUES(?,?,?,?,?)",
                (task["chat_id"],"assistant","",json.dumps(metadata,ensure_ascii=False),time.time()))
            task["history_message_id"]=cursor.lastrowid
            task["status"]="queued" if active else "running"
            task["worker_id"]="worker-1"
            c.execute("INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,created_at,updated_at,request_key) VALUES(?,?,?,?,?,?,?,?)",
                (task["task_id"],owner,task["chat_id"],task["status"],json.dumps(task,ensure_ascii=False),task["created_at"],time.time(),request_key))
        tasks[task["task_id"]]=task
        self.ensure_runner(owner)
        return task

    def ensure_runner(self, owner):
        if self.closing:return
        if owner not in self.runners or self.runners[owner].done():
            self.runners[owner]=asyncio.create_task(self._run(owner))

    async def _execute(self, task):
        if self.execute:return await self.execute(task)
        try:
            from .task_runtime import run_nl_task, run_onboarding_task
        except ImportError:
            from task_runtime import run_nl_task, run_onboarding_task
        if not task["device_ids"]:
            await run_onboarding_task(task["task_id"],task["user_id"],task["message"],task["chat_id"])
        else:
            await run_nl_task(task["task_id"],task["user_id"],task["message"],task["device_ids"],task["chat_id"])

    async def _run(self, owner):
        try:
            while not self.closing:
                with db.get_db() as c:
                    row=c.execute("SELECT * FROM worker_jobs WHERE owner_user_id=? AND state IN ('running','waiting_confirmation')",(owner,)).fetchone()
                if row is None: return
                task=tasks.get(row["task_id"])
                if task is None:
                    task=restore_task(dict(row));tasks[task["task_id"]]=task
                try:
                    try:
                        from .llm_usage import LLM_ENTITY
                        from .controller_shared import WORKER_MEMORY_QUERY
                        from .memory_intent_guard import ORIGINAL_WORKER_REQUEST
                    except ImportError:
                        from llm_usage import LLM_ENTITY
                        from controller_shared import WORKER_MEMORY_QUERY
                        from memory_intent_guard import ORIGINAL_WORKER_REQUEST
                    if task.get('context_history') is not None:
                        try:
                            from .worker_context import build_worker_context
                        except ImportError:
                            from worker_context import build_worker_context
                        task['worker_context']=build_worker_context(task['user_id'],task['chat_id'],task.get('original_request') or task['message'],
                            task['device_ids'],task['context_history'],task.get('source_task_ids') or [],
                            objective=task.get('proposed_objective') or '',context_summary=task.get('proposed_context_summary') or '')
                    task["admitted_at"]=task["created_at"]
                    task["created_at"]=time.time()
                    task["worker_started_at"]=task["created_at"]
                    role_token=LLM_ENTITY.set("worker")
                    memory_token=WORKER_MEMORY_QUERY.set(task["message"])
                    human_token=ORIGINAL_WORKER_REQUEST.set(task.get("original_request") or task["message"])
                    try:
                        await asyncio.wait_for(self._execute(task),timeout=3600)
                    finally:
                        LLM_ENTITY.reset(role_token);WORKER_MEMORY_QUERY.reset(memory_token);ORIGINAL_WORKER_REQUEST.reset(human_token)
                    # Ordinary confirmation unwinds its controller; retain the slot
                    # until the existing confirmation handler produces a terminal result.
                    while task.get("status") not in TERMINAL and not self.closing:
                        with db.get_db() as c:
                            c.execute("UPDATE worker_jobs SET state=? WHERE task_id=?",("waiting_confirmation" if task.get("status")=="confirm" else "running",task["task_id"]))
                        if time.time()-task["created_at"]>3600:
                            task.update(status="unknown",worker_error_code="confirmation_expired");break
                        await asyncio.sleep(.1)
                    if self.closing: return
                except asyncio.CancelledError:
                    task.update(status="unknown",worker_error_code="server_interrupted")
                    _finalize_report(task);raise
                except asyncio.TimeoutError:
                    task.update(status="unknown",worker_error_code="worker_deadline_outcome_unknown")
                except Exception:
                    task.update(status="failed",worker_error_code="worker_exception")
                _finalize_report(task)
                with db.get_db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    next_row=c.execute("SELECT * FROM worker_jobs WHERE owner_user_id=? AND state='queued' ORDER BY created_at, rowid LIMIT 1",(owner,)).fetchone()
                    if next_row: c.execute("UPDATE worker_jobs SET state='running',updated_at=? WHERE task_id=?",(time.time(),next_row["task_id"]))
                if next_row:
                    next_task=tasks.get(next_row["task_id"]) or restore_task(dict(next_row))
                    next_task["status"]="running";tasks[next_row["task_id"]]=next_task
                else:return
        finally:
            # A new submit can occur immediately after this coroutine returns;
            # ensure_runner checks done(), rather than leaving a stale busy flag.
            pass

    async def cancel(self, task_id, owner):
        job=owned_job(task_id,owner)
        if not job:raise ValueError("task_not_found")
        task=tasks.get(task_id) or restore_task(job);tasks[task_id]=task
        if job["state"]=="queued":
            mark_task_cancelled(task_id,answer="Ожидающая задача отменена до исполнения.",commands=[])
            task["worker_error_code"]="cancelled_before_execution";_finalize_report(task)
        elif job["state"] in ACTIVE_STATES:
            request_task_cancel(task_id,owner)
            for field,decision in [("_pipeline_plan_future",{"action":"cancel"}),("_pipeline_confirm_future",False)]:
                future=task.get(field)
                if future is not None and not future.done():future.set_result(decision)
            if task.get("confirm_data") and "_pipeline_confirm_future" not in task:
                mark_task_cancelled(task_id,answer="Остановлено пользователем.")
        return task

    def acquire_instance(self):
        # The existing agent WS registry is process-local. Fail closed rather than
        # let a second server recover/reassign a live process's Worker jobs.
        if self.instance_lock is not None:return False
        path=db.DB_PATH.with_name(db.DB_PATH.name+".ow.lock")
        handle=open(path,"a+b")
        try:
            if sys.platform=="win32":
                import msvcrt
                if handle.tell()==0:handle.write(b"0");handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close();raise RuntimeError("OW requires a single IRU server process for this database")
        self.instance_lock=handle
        return True

    async def recover(self):
        if not self.acquire_instance():return
        self.closing=False
        self.runners={}
        init_worker_storage()
        with db.get_db() as c:
            rows=[dict(row) for row in c.execute("SELECT * FROM worker_jobs WHERE state IN ('queued','running','waiting_confirmation')")]
        for row in rows:
            task=restore_task(row);tasks[task["task_id"]]=task
            task.update(status="cancelled" if row["state"]=="queued" else "unknown",
                worker_error_code="server_restarted_not_executed" if row["state"]=="queued" else "server_restarted_outcome_unknown")
            _finalize_report(task)

    async def shutdown(self):
        self.closing=True
        loop=asyncio.get_running_loop()
        current=[runner for runner in self.runners.values() if runner.get_loop() is loop]
        for runner in current:runner.cancel()
        await asyncio.gather(*current,return_exceptions=True)
        self.runners={}
        if self.instance_lock is not None:
            self.instance_lock.close();self.instance_lock=None


scheduler=WorkerScheduler()

import asyncio
import base64
import json
from copy import deepcopy
import logging
import time
import uuid
from io import BytesIO
from typing import Optional, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

try:
    from ..api_support import _is_admin, check_ip_rate_limit, check_rate_limit, get_current_user
    from ..database import (
        PLAN_LIMITS,
        add_audit_log,
        add_message,
        message_task_metadata,
        get_messages,
        add_user_fact,
        check_daily_command_limit,
        check_device_limit,
        create_chat,
        delete_memory_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
        get_user_facts,
        get_plan_trial_used,
        get_user_device_profiles,
        get_user_plan,
        increment_daily_commands,
        set_plan_trial_used,
    )
    from ..runtime_state import (
        _dk,
        _short_did,
        cleanup_old_tasks,
        create_download_token,
        devices,
        download_tokens,
        get_user_devices,
        mark_task_cancelled,
        is_task_cancel_requested,
        TASK_TTL,
        mark_suggested_fact_declined,
        mark_plan_declined,
        request_task_cancel,
        tasks,
        TOKEN_TTL,
    )
    from ..python_toolchain import PythonToolchainReceipt, validate_toolchain_fact_against_receipt
    from ..task_runtime import run_nl_task, run_onboarding_task, send_command_to_agent
except ImportError:
    from api_support import _is_admin, check_ip_rate_limit, check_rate_limit, get_current_user
    from database import (
        PLAN_LIMITS,
        add_audit_log,
        add_message,
        message_task_metadata,
        get_messages,
        add_user_fact,
        check_daily_command_limit,
        check_device_limit,
        create_chat,
        delete_memory_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
        get_user_facts,
        get_plan_trial_used,
        get_user_device_profiles,
        get_user_plan,
        increment_daily_commands,
        set_plan_trial_used,
    )
    from runtime_state import (
        _dk,
        _short_did,
        cleanup_old_tasks,
        create_download_token,
        devices,
        download_tokens,
        get_user_devices,
        mark_task_cancelled,
        is_task_cancel_requested,
        TASK_TTL,
        mark_suggested_fact_declined,
        mark_plan_declined,
        request_task_cancel,
        tasks,
        TOKEN_TTL,
    )
    from python_toolchain import PythonToolchainReceipt, validate_toolchain_fact_against_receipt
    from task_runtime import run_nl_task, run_onboarding_task, send_command_to_agent


try:
    from ..command_confirmation import confirmed_command_outcome
    from ..tool_completion import execute_cmd_outcome_marker
    from ..run_journal import make_run_step, append_tool_step, append_answer_step, validate_answer_text_payload, diagnostic_context_for_task
except ImportError:
    from command_confirmation import confirmed_command_outcome
    from tool_completion import execute_cmd_outcome_marker
    from run_journal import make_run_step, append_tool_step, append_answer_step, validate_answer_text_payload, diagnostic_context_for_task

try:
    from ..worker_scheduler import scheduler, owned_job, restore_task
    from ..worker_reports import build_worker_report
    from ..orchestrator import run_turn, restore_dialogue
except ImportError:
    from worker_scheduler import scheduler, owned_job, restore_task
    from worker_reports import build_worker_report
    from orchestrator import run_turn, restore_dialogue


async def execute_worker(task):
    if task["device_ids"]:
        await run_nl_task(task["task_id"],task["user_id"],task["message"],task["device_ids"],task["chat_id"])
    else:
        await run_onboarding_task(task["task_id"],task["user_id"],task["message"],task["chat_id"])

scheduler.execute = execute_worker


async def submit_worker(user, chat_id, message, target_ids, modes, *, request_key=None, context_summary="", objective="", broadcast=False, execution_mode="auto", source_task_ids=(), history_snapshot=None):
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("duplicate_target_device")
    for did in target_ids:
        dev = devices.get(did)
        if not dev or dev.get("user_id") != user["id"]:
            raise ValueError("device_not_owned_or_unavailable")
    task_id = str(uuid.uuid4())
    task = {"task_id":task_id,"user_id":user["id"],"chat_id":chat_id,"message":message,"original_request":message,"proposed_objective":objective,"proposed_context_summary":context_summary,"orchestrated":bool(objective),"broadcast":broadcast,
        "device_ids":target_ids,"status":"running","results":{},"answer":None,"commands":None,
        "modes":modes,"created_at":time.time(),"kind":"worker"}
    try:
        from ..worker_context import build_worker_context, capture_history
    except ImportError:
        from worker_context import build_worker_context, capture_history
    task['orchestrator_execution_mode']=execution_mode if objective else 'auto'
    task['source_task_ids']=list(source_task_ids)
    task['context_history']=history_snapshot if history_snapshot is not None else capture_history(chat_id)
    task['worker_context']=build_worker_context(user['id'],chat_id,message,target_ids,task['context_history'],source_task_ids,
        objective=task['proposed_objective'],context_summary=task['proposed_context_summary'])
    return await scheduler.submit(task, request_key=request_key)


router = APIRouter()
logger = logging.getLogger("iru.run_plan")


class DirectCommand(BaseModel):
    device_id: str
    action: str
    params: dict = {}


class NLCommand(BaseModel):
    device_id: str = ""
    message: str
    chat_id: int | None = None
    broadcast: bool = False
    device_ids: list[str] = []
    modes: dict = {}
    orchestrate: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class RunPlanBody(BaseModel):
    original_request: str
    device_id: Optional[str] = None
    confirmed: bool = False
    voice_source_task_id: str | None = None


class MemoryFactDeleteBody(BaseModel):
    id: int
    source: str
    device_id: str | None = None


class MemoryFactCreateBody(BaseModel):
    text: str
    category: str | None = "general"


def _format_user_fact(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "text": row.get("fact_text") or row.get("text") or "",
        "category": row.get("category"),
        "source": "user",
        "created_at": row.get("created_at"),
        "updated_at": row.get("created_at"),
    }


def _memory_facts_for_profile(user: dict, profile: dict | None) -> list[dict]:
    stats = _memory_stats_for_profile(user, profile)
    user_rows = {
        row.get("id"): _format_user_fact(row)
        for row in get_user_facts(str(user["id"]))
    }
    facts = []
    for fact in stats.get("facts_list", []):
        if fact.get("source") == "user" and fact.get("id") in user_rows:
            facts.append(user_rows[fact.get("id")])
        else:
            facts.append({
                "id": fact.get("id"),
                "text": fact.get("text") or fact.get("fact") or "",
                "category": fact.get("category"),
                "source": fact.get("source") or "user",
                "created_at": fact.get("created_at"),
                "updated_at": fact.get("updated_at") or fact.get("created_at"),
            })
    return facts


def _owned_device_profile(user: dict, device_id: str | None = None, machine_guid: str | None = None) -> dict | None:
    if device_id:
        profile = get_device_profile(_short_did(device_id), user_id=user["id"])
        if profile and (profile.get("user_id") == user["id"] or _is_admin(user)):
            return profile
        return None

    profiles = get_user_device_profiles(user["id"])
    if machine_guid:
        return next((p for p in profiles if p.get("machine_guid") == machine_guid), None)
    return profiles[0] if profiles else None


def _memory_stats_for_profile(user: dict, profile: dict | None) -> dict:
    machine_guid = profile.get("machine_guid") if profile else None
    return get_memory_stats(machine_guid, str(user["id"]) if user.get("id") else None,
                            profile.get("device_id") if profile else None)


class RawCommand(BaseModel):
    command: str
    device_id: str = ""
    broadcast: bool = False


def _build_download_headers(filename: str) -> dict[str, str]:
    safe_name = filename or "file"
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_" for ch in safe_name).strip(" .")
    if not ascii_name:
        ascii_name = "file"
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe_name)}"
    }


@router.post("/command")
async def direct_command(cmd: DirectCommand, request: Request):
    user = get_current_user(request)
    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    if not _is_admin(user):
        cmd_limit = check_daily_command_limit(user["id"])
        if not cmd_limit["allowed"]:
            return {
                "status": "error",
                "error": f"Дневной лимит команд исчерпан ({cmd_limit['used']}/{cmd_limit['limit']}). Обновите тариф для снятия ограничений.",
            }
        increment_daily_commands(user["id"])

    cmd_dk = _dk(user["id"], cmd.device_id)
    if cmd_dk not in devices:
        return {"status": "error", "error": "Устройство не найдено или нет доступа"}
    try:
        result = await send_command_to_agent(cmd_dk, cmd.action, cmd.params)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/nl_command")
@router.post("/api/chat")
async def nl_command(cmd: NLCommand, request: Request):
    user = get_current_user(request)
    cleanup_old_tasks()

    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    user_devs = get_user_devices(user["id"])
    print(
        f"[nl_command] user_id={user['id']}, user='{user['name']}', "
        f"cmd.device_id='{cmd.device_id}', user_devs={list(user_devs.keys())}, "
        f"all_devices_keys={list(devices.keys())}"
    )

    chat_id = cmd.chat_id
    if not chat_id:
        title = cmd.message[:50].strip() or "Новый чат"
        chat = create_chat(user["id"], title)
        chat_id = chat["id"]
    else:
        chat = get_chat(chat_id, user["id"])
        if not chat:
            return {"status": "error", "error": "Чат не найден"}

    if cmd.orchestrate:
        try:
            from ..worker_context import capture_history
        except ImportError:
            from worker_context import capture_history
        history_snapshot=capture_history(chat_id)
        async def delegate(choice, request_key):
            if choice.scope=="device" and cmd.device_id and _dk(user["id"],cmd.device_id) not in user_devs:
                raise ValueError("selected_device_not_owned_or_connected")
            if cmd.modes.get("pipeline"):
                raise ValueError("plan_requires_existing_review_flow")
            targets=[_dk(user["id"],did) for did in choice.target_device_ids]
            if cmd.broadcast:
                targets=list(user_devs)
            if choice.scope=="server":
                if targets:raise ValueError("server_scope_has_device_targets")
            elif not targets:
                raise ValueError("target_device_required")
            return await submit_worker(user,chat_id,cmd.message,targets,cmd.modes,request_key=request_key,
                context_summary=choice.context_summary,objective=choice.objective,broadcast=cmd.broadcast,
                execution_mode=choice.execution_mode,source_task_ids=choice.source_task_ids,history_snapshot=history_snapshot)
        try:
            return await run_turn(cmd,user,chat_id,delegate)
        except ValueError:
            return {"status":"error","error":"Повтор запроса изменён или состояние недоступно. Новое выполнение не начато."}

    add_message(chat_id, "user", cmd.message)

    if not user_devs and not cmd.device_id:
        try:
            task=await submit_worker(user,chat_id,cmd.message,[],cmd.modes or {},request_key=cmd.request_id)
        except ValueError as exc:
            return {"status":"error","error":str(exc)}
        return {"status":"ok","task_id":task["task_id"],"chat_id":chat_id,"device_ids":[],"worker_id":"worker-1"}

    if cmd.broadcast:
        target_ids = list(user_devs.keys())
    elif cmd.device_ids:
        target_ids = [_dk(user["id"], did) for did in cmd.device_ids if _dk(user["id"], did) in user_devs]
    else:
        cmd_dk = _dk(user["id"], cmd.device_id)
        if cmd_dk not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено или нет доступа"}
        target_ids = [cmd_dk]

    if not target_ids:
        return {"status": "error", "error": "Нет доступных устройств"}

    try:
        task = await submit_worker(user,chat_id,cmd.message,target_ids,cmd.modes or {},request_key=cmd.request_id,broadcast=cmd.broadcast)
    except ValueError as exc:
        return {"status":"error","error":str(exc)}
    return {"status":"ok","task_id":task["task_id"],"chat_id":chat_id,"device_ids":target_ids,"worker_id":"worker-1","worker_status":task["status"]}


@router.get("/api/operations")
async def api_operations(request: Request):
    """Read-only owner-scoped FIFO snapshot; no execution or queue reconstruction."""
    user = get_current_user(request)
    try:
        from ..worker_scheduler import list_jobs
        from ..response_presentation import normalized_worker_report
    except ImportError:
        from worker_scheduler import list_jobs
        from response_presentation import normalized_worker_report
    items = []
    for job in list_jobs(user["id"], limit=20, queue_first=True):
        live = tasks.get(job["task_id"])
        task = live if live and live.get("user_id") == user["id"] else restore_task(job)
        report = normalized_worker_report(task, task.get("worker_report"))
        items.append({"task_id": job["task_id"], "chat_id": job["chat_id"],
            "title": (task.get("message") or "Задача")[:160],
            "status": report["status"], "device_ids": report["target_device_ids"],
            "summary": report["summary"],
            "created_at": job["created_at"], "updated_at": job["updated_at"],
            "can_cancel": report["status"] in {"queued", "running", "waiting_confirmation"},
            "waiting_confirmation": report["status"] == "waiting_confirmation"})
    return {"status": "ok", "operations": items}


@router.get("/api/tasks")
async def api_list_tasks(request: Request):
    user = get_current_user(request)
    cleanup_old_tasks()
    user_tasks = [task for task in tasks.values() if task["user_id"] == user["id"]]
    user_tasks.sort(key=lambda task: task["created_at"], reverse=True)
    return {
        "status": "ok",
        "tasks": [
            {
                "task_id": task["task_id"],
                "chat_id": task["chat_id"],
                "message": task["message"][:80],
                "device_ids": task["device_ids"],
                "status": task["status"],
                "answer": task.get("answer"),
                "commands": task.get("commands"),
                "created_at": task["created_at"],
            }
            for task in user_tasks[:20]
        ],
    }


@router.get("/api/memory/stats")
async def api_memory_stats(request: Request, device_id: str | None = None):
    user = get_current_user(request)
    profile = _owned_device_profile(user, device_id)
    return {"status": "ok", "memory_stats": _memory_stats_for_profile(user, profile)}


@router.get("/api/memory/facts")
async def api_memory_facts(request: Request, device_id: str | None = None):
    user = get_current_user(request)
    profile = _owned_device_profile(user, device_id)
    return {"status": "ok", "facts": _memory_facts_for_profile(user, profile)}


@router.post("/api/memory/facts")
async def api_add_memory_fact(body: MemoryFactCreateBody, request: Request):
    user = get_current_user(request)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Memory fact text is required")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Memory fact text is too long")
    category = (body.category or "general").strip() or "general"
    if len(category) > 64:
        raise HTTPException(status_code=400, detail="Memory fact category is too long")

    fact_id = add_user_fact(str(user["id"]), text, category)
    fact = next(
        (_format_user_fact(row) for row in get_user_facts(str(user["id"])) if row.get("id") == fact_id),
        {"id": fact_id, "text": text, "category": category, "source": "user", "created_at": None, "updated_at": None},
    )
    return {"status": "ok", "fact": fact}


@router.delete("/api/memory/facts/{fact_id}")
async def api_delete_memory_fact_v1(
    fact_id: int,
    request: Request,
    source: str = "user",
    device_id: str | None = None,
):
    user = get_current_user(request)
    source = (source or "user").strip().lower()
    if source not in {"user", "device"}:
        raise HTTPException(status_code=400, detail="Invalid memory source")
    if source == "device" and not device_id:
        raise HTTPException(status_code=400, detail="Device id required for device memory source")

    profile = _owned_device_profile(user, device_id)
    machine_guid = profile.get("machine_guid") if profile else None
    if source == "device" and not machine_guid:
        raise HTTPException(status_code=404, detail="Device memory source not found")

    ok = delete_memory_fact(str(user["id"]), fact_id, source, machine_guid,
                            profile.get("device_id") if profile else None)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory fact not found")
    return {"status": "ok", "facts": _memory_facts_for_profile(user, profile)}


@router.post("/api/memory/facts/delete")
async def api_delete_memory_fact(body: MemoryFactDeleteBody, request: Request):
    user = get_current_user(request)
    source = (body.source or "").strip().lower()
    if source not in {"user", "device"}:
        raise HTTPException(status_code=400, detail="Invalid memory source")
    if source == "device" and not body.device_id:
        raise HTTPException(status_code=400, detail="Device id required for device memory source")

    profile = _owned_device_profile(user, body.device_id)
    machine_guid = profile.get("machine_guid") if profile else None
    if source == "device" and not machine_guid:
        raise HTTPException(status_code=404, detail="Device memory source not found")

    ok = delete_memory_fact(str(user["id"]), body.id, source, machine_guid,
                            profile.get("device_id") if profile else None)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory fact not found")

    return {"status": "ok", "memory_stats": _memory_stats_for_profile(user, profile)}


@router.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if task is None:
        job = owned_job(task_id,user["id"])
        if job:task=restore_task(job)
        else:task=restore_dialogue(task_id,user["id"])
        if task:tasks[task_id]=task
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    memory_stats = get_memory_stats(None, str(user["id"]))
    if task.get("device_ids"):
        try:
            first_did = _short_did(task["device_ids"][0])
            profile = get_device_profile(first_did, user_id=user["id"])
            if profile and profile.get("machine_guid"):
                memory_stats = get_memory_stats(profile["machine_guid"], str(user["id"]) if user.get("id") else None, profile["device_id"])
        except Exception:
            pass

    presentation = message_task_metadata(task)
    saved_presentation = task.get("history_metadata")
    if isinstance(saved_presentation, dict):
        for key in ("taskMode", "taskElapsedMs", "taskStatus"):
            value = saved_presentation.get(key)
            if (key in {"taskMode", "taskStatus"} and isinstance(value, str)) or (key == "taskElapsedMs" and type(value) is int and value >= 0):
                presentation[key] = value
    try:
        from ..response_presentation import worker_presentation, normalized_worker_report
    except ImportError:
        from response_presentation import worker_presentation, normalized_worker_report
    report = normalized_worker_report(task,task.get("worker_report")) if task.get("worker_id") else task.get("worker_report")
    human = worker_presentation(task,report) if task.get("worker_id") else {
        "conversational_response":task.get("answer"),"execution_details":task.get("execution_details") or ""}
    response_task = {
        **human,
        "task_id": task["task_id"],
        "chat_id": task["chat_id"],
        "message": task["message"],
        "device_ids": task["device_ids"],
        "status": task["status"],
        "answer": task.get("answer"),
        "commands": task.get("commands"),
        "tasks": task.get("tasks", []),
        "task_receipt": task.get("task_receipt"),
        "worker_id":task.get("worker_id"),
        "worker_report":report,
        "kind":task.get("kind"),
        "presentation_status": report["status"] if task.get("worker_id") else task["status"] if task["status"] in {"error", "failed", "blocked", "cancelled"} else presentation.get("taskStatus"),
        "task_mode": presentation["taskMode"],
        "elapsed_ms": presentation["taskElapsedMs"],
        "diagnostic_trace": task.get("diagnostic_trace", []),
        "current_step": task.get("current_step"),
        "results": task.get("results", {}),
        "overall_status": task.get("overall_status"),
        "confirm_data": task.get("confirm_data"),
        "plan_review": task.get("plan_review"),
        "created_at": task["created_at"],
        "memory_stats": memory_stats,
        "suggested_fact": task.get("suggested_fact"),
    }
    if task.get("plan_suggestion"):
        response_task["plan_suggestion"] = task["plan_suggestion"]
        response_task["plan_original_request"] = task.get("plan_original_request", "")
        user_plan = get_user_plan(user["id"])
        if user_plan == "free" and not _is_admin(user):
            response_task["plan_trial_used"] = bool(get_plan_trial_used(user["id"]))
    if task.get("auto_plan"):
        response_task["auto_plan"] = True

    return {"status": "ok", "task": response_task}


@router.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task.get("worker_id") and task.get("status") == "queued":
        await scheduler.cancel(task_id,user["id"])
        return {"status":"ok","task_status":"cancelled","cancel_requested":True,"message":"Ожидающая задача отменена."}
    previous_status = task.get("status")
    if previous_status in {"done", "error", "completed", "completed_with_recovery", "failed", "cancelled", "blocked"}:
        return {"status": "ok", "task_status": previous_status, "cancel_requested": bool(task.get("cancel_requested"))}
    if previous_status == "confirm":
        plan_decision = task.get("_pipeline_plan_future")
        if plan_decision is not None and not plan_decision.done():
            plan_decision.set_result({"action": "cancel"})
        decision = task.get("_pipeline_confirm_future")
        if decision is not None and not decision.done():
            decision.set_result(False)
        updated = mark_task_cancelled(task_id, answer="Остановлено пользователем.", commands=task.get("commands", []))
        return {
            "status": "ok",
            "task_status": updated.get("status") if updated else "cancelled",
            "cancel_requested": True,
            "message": "Остановлено пользователем.",
        }
    updated = request_task_cancel(task_id, user["id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {
        "status": "ok",
        "task_status": updated.get("status"),
        "cancel_requested": True,
        "message": "Остановка запрошена. Текущий инструмент может завершиться с задержкой.",
    }


class PlanReviewBody(BaseModel):
    revision: str = Field(min_length=1, max_length=64)
    action: Literal["approve", "revise"]
    changes: str = Field(default="", max_length=4000)


@router.post("/api/tasks/{task_id}/review-plan")
async def api_review_plan(task_id: str, body: PlanReviewBody, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Задача не найдена")
    review = task.get("plan_review") or {}
    decision = task.get("_pipeline_plan_future")
    if (task.get("status") != "confirm" or decision is None or decision.done()
            or review.get("revision") != body.revision):
        raise HTTPException(409, "Этот вариант плана уже не ожидает ответа. Обновите состояние задачи.")
    changes = body.changes.strip()
    if body.action == "revise" and not changes:
        raise HTTPException(400, "Опишите, что нужно изменить в плане.")
    task["status"] = "running"
    decision.set_result({"action": body.action, "changes": changes})
    return {"status": "ok"}


class CommandDecisionBody(BaseModel):
    confirmation_id: str = Field(min_length=1, max_length=64)
    accepted: bool
    via_voice: bool = False


@router.post("/api/tasks/{task_id}/command-decision")
async def api_command_decision(task_id: str, body: CommandDecisionBody, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Задача не найдена")
    data = task.get("confirm_data") or {}
    if (task.get("status") != "confirm" or task.get("plan_review")
            or data.get("confirmation_id") != body.confirmation_id):
        raise HTTPException(409, "Это подтверждение команды уже не актуально.")
    if body.via_voice and (not data.get("voice_allowed") or data.get("kind") != "command"):
        raise HTTPException(403, "Удаление и опасные команды подтверждаются только кнопкой в чате.")
    if body.accepted:
        return await api_confirm_task(task_id, request)
    return await api_deny_task(task_id, request)


@router.post("/api/tasks/{task_id}/confirm")
async def api_confirm_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "confirm":
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    if task.get("plan_review"):
        raise HTTPException(409, "Подтвердите текущий вариант плана через карточку плана.")

    decision = task.get("_pipeline_confirm_future")
    if decision is not None:
        if decision.done():
            raise HTTPException(409, detail="Подтверждение уже обработано")
        task["status"] = "running"
        decision.set_result(True)
        return {"status": "ok"}

    if (task.get("modes") or {}).get("pipeline"):
        # Never use the single-command completion path for an orphaned PLAN.
        raise HTTPException(409, detail="Продолжение PLAN недоступно. Запустите исходную задачу заново.")

    # Legacy ordinary confirmation: the controller frame has already unwound.
    # Never restart it, replay old actions, or equate one command with the whole goal.
    if is_task_cancel_requested(task_id):
        raise HTTPException(409, detail="Задача отменена; команда не будет выполнена.")
    created = task.get("created_at")
    if type(created) not in (int, float) or not 0 <= time.time() - created <= TASK_TTL:
        raise HTTPException(409, detail="Подтверждение устарело. Запустите исходную задачу заново.")
    confirm_data = deepcopy(task.get("confirm_data") or {})
    short_did = confirm_data.get("device_id")
    params = confirm_data.get("params")
    if (not isinstance(short_did, str) or not short_did or not isinstance(params, dict)
            or not isinstance(params.get("command"), str) or not params["command"].strip()
            or confirm_data.get("command") != params["command"]
            or set(params) - {"command", "timeout", "shell"}):
        raise HTTPException(409, detail="Нет точной исполняемой команды. Продолжение исходной задачи недоступно.")
    chat_id = task.get("chat_id")
    confirm_dk = _dk(user["id"], short_did) if ":" not in short_did else short_did
    execution_token = object()
    # Claim synchronously, before scheduling or any await: approval is one-shot.
    task["status"] = "running"
    task["_confirmed_execution_token"] = execution_token
    task.pop("confirm_data", None)

    def active():
        return tasks.get(task_id) is task and task.get("_confirmed_execution_token") is execution_token

    def finish(result, outcome, reason):
        if not active():
            return
        journal = list(task.get("commands") or [])
        # Keep evidence metadata, not literal commands, stdout/stderr or exceptions.
        evidence = {"status": outcome, "confirmation_outcome": outcome, "reason": reason}
        if isinstance(result, dict):
            code = result.get("returncode")
            if type(code) is int or code == "0":
                evidence["returncode"] = code
            evidence["outcome_marker"] = execute_cmd_outcome_marker(result)
            for name in ("stdout", "stderr"):
                if isinstance(result.get(name), str):
                    evidence[name + "_chars"] = len(result[name])
        if outcome == "failed":
            evidence["error"] = reason
        entry = append_tool_step(journal, make_run_step(journal=journal, tool_name="execute_cmd",
            command="[tool] execute_cmd (confirmed)", target_device_id=short_did,
            result=evidence, status=outcome, summary=f"confirmed_execution={outcome}; original_goal=not_verified"))
        if outcome == "success":
            text = "Подтверждённая команда выполнена по проверенному результату. "
        elif reason == "device_unavailable":
            text = "Устройство отключено или недоступно. Подтверждённая команда не выполнялась. "
        elif outcome == "failed":
            code = evidence.get("returncode")
            text = "Подтверждённая команда завершилась с ошибкой" + (f" (код {code})" if code is not None else "") + ". "
        else:
            text = "Исход выполнения подтверждённой команды не подтверждён. Команда могла выполниться; автоматически её не повторяю. "
        text += "Продолжение исходной задачи недоступно: дальнейшие шаги не выполнялись, завершение всей задачи не подтверждено."
        cancelled = is_task_cancel_requested(task_id)
        if cancelled:
            text = "Задача остановлена пользователем. " + text
        payload = {"answer_type": "error_report" if outcome == "failed" else "partial_report",
            "text": text, "basis": [entry["step_id"]], "self_check": {
                "depends_on_current_external_state": True, "claims_completed_action": outcome == "success",
                "has_sufficient_evidence": outcome != "unknown", "missing_evidence_question":
                    "Продолжение controller loop и выполнение всей исходной цели не подтверждены."}}
        append_answer_step(journal, "answer_text", validate_answer_text_payload(payload, journal), target_device_id=short_did)
        task["commands"] = journal
        task["answer"] = text
        task["status"] = "cancelled" if cancelled else "failed" if outcome == "failed" else "blocked"
        task["overall_status"] = "cancelled" if cancelled else "failed" if outcome == "failed" else "partial_failure"
        task["task_receipt"] = {"task_status": "cancelled" if cancelled else "failed" if outcome == "failed" else "partial",
            "answer_source": "confirmation_result", "command_outcome": outcome, "goal_completed": False,
            "continuation_status": "unavailable", "terminal_reason": reason, "basis": [entry["step_id"]]}
        task["history_metadata"] = message_task_metadata(task, task_id=task_id)
        try:
            saved_message=add_message(chat_id, "assistant", text, journal, task_metadata=task["history_metadata"],message_id=task.get("history_message_id"))
            if isinstance(saved_message,dict):task["history_message_id"]=saved_message.get("id")
        except Exception as exc:
            logger.warning("confirmation result persistence failed task_id=%s error_type=%s", task_id, type(exc).__name__)

    async def execute_confirmed():
        try:
            if not active():
                return
            if is_task_cancel_requested(task_id):
                mark_task_cancelled(task_id, answer="Остановлено пользователем.", commands=task.get("commands") or [])
                return
            if task.get("status") != "running" or time.time() - created > TASK_TTL:
                task["status"] = "blocked"
                task["answer"] = "Подтверждение устарело. Команда не выполнялась; исходная задача не завершена."
                task["task_receipt"] = {"task_status":"blocked", "command_outcome":"not_executed",
                    "goal_completed":False, "continuation_status":"unavailable", "terminal_reason":"confirmation_expired"}
                return
            dev = devices.get(confirm_dk)
            if not dev or dev.get("user_id") != user["id"] or not dev.get("ws"):
                finish(None, "failed", "device_unavailable")
                return
            try:
                with diagnostic_context_for_task(task_id,task):
                    result = await send_command_to_agent(confirm_dk, "execute_cmd", params,
                                                         user_id=user["id"], skip_confirm=True)
            except Exception:
                # A transport exception can occur after dispatch. Never retry blindly.
                finish(None, "unknown", "confirmed_transport_outcome_unknown")
                return
            outcome = confirmed_command_outcome(result)
            finish(result, outcome, "confirmation_continuation_unavailable" if outcome == "success"
                   else "confirmed_execution_failed" if outcome == "failed" else "confirmed_execution_unknown")
        finally:
            if active():
                task.pop("_confirmed_execution_token", None)

    asyncio.create_task(execute_confirmed())
    return {"status": "ok"}


@router.post("/api/tasks/{task_id}/remember")
async def api_remember_fact(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    suggested_fact = task.get("suggested_fact")
    if not suggested_fact:
        return {"status": "error", "error": "Нет предложенного факта"}
    receipt = PythonToolchainReceipt.from_any(task.get("python_toolchain_receipt"))
    allowed_fact, corrected_fact = validate_toolchain_fact_against_receipt(suggested_fact["text"], receipt)
    if not allowed_fact:
        return {"status": "error", "error": "Python toolchain fact is not backed by a verified receipt"}
    fact_id = add_user_fact(
        user_id=str(user["id"]),
        text=corrected_fact or suggested_fact["text"],
        category=suggested_fact.get("category"),
    )
    task.pop("suggested_fact", None)
    profile = None
    if task.get("device_ids"):
        profile = _owned_device_profile(user, _short_did(task["device_ids"][0]))
    return {"status": "ok", "fact_id": fact_id, "memory_stats": _memory_stats_for_profile(user, profile)}


@router.post("/api/tasks/{task_id}/decline_fact")
async def api_decline_fact(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    suggested_fact = task.get("suggested_fact")
    if not suggested_fact:
        return {"status": "ok"}
    mark_suggested_fact_declined(
        user["id"],
        task.get("chat_id"),
        suggested_fact.get("text", ""),
        suggested_fact.get("category"),
    )
    task.pop("suggested_fact", None)
    task["suggested_fact_declined"] = True
    profile = None
    if task.get("device_ids"):
        profile = _owned_device_profile(user, _short_did(task["device_ids"][0]))
    return {"status": "ok", "memory_stats": _memory_stats_for_profile(user, profile)}


@router.post("/api/tasks/{task_id}/decline_plan")
async def api_decline_plan_suggestion(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    original_request = task.get("plan_original_request") or task.get("message") or ""
    chat_id = task.get("chat_id")
    if not chat_id or not original_request:
        raise HTTPException(status_code=400, detail="Нет исходного запроса для отказа от плана")

    mark_plan_declined(chat_id, original_request)
    task.pop("plan_suggestion", None)
    task["plan_declined"] = True
    return {"status": "ok"}


@router.post("/api/tasks/{task_id}/deny")
async def api_deny_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "confirm":
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    plan_decision = task.get("_pipeline_plan_future")
    if plan_decision is not None:
        request_task_cancel(task_id, user["id"])
        if not plan_decision.done():
            plan_decision.set_result({"action": "cancel"})
        return {"status": "ok"}

    decision = task.get("_pipeline_confirm_future")
    if decision is not None:
        request_task_cancel(task_id, user["id"])
        if not decision.done():
            decision.set_result(False)
        return {"status": "ok"}

    chat_id = task.get("confirm_data", {}).get("chat_id", task.get("chat_id"))
    task["status"] = "done"
    task["answer"] = "Команда отменена пользователем."
    if task.get("worker_id"):
        # Denial is a known cancellation, not an unverified completed Worker.
        mark_task_cancelled(task_id, answer=task["answer"], commands=task.get("commands") or [])
        task["task_receipt"] = {"task_status":"cancelled", "goal_completed":False,
            "command_outcome":"not_executed", "terminal_reason":"confirmation_denied"}
    task.pop("confirm_data", None)
    task["history_metadata"] = message_task_metadata({**task, "status": "cancelled"}, task_id=task_id)
    add_message(chat_id, "assistant", task["answer"], task.get("commands", []), task_metadata=task["history_metadata"],message_id=task.get("history_message_id"))
    return {"status": "ok"}


@router.post("/api/run_plan/{chat_id}")
async def api_run_plan(chat_id: int, body: RunPlanBody, request: Request):
    user = get_current_user(request)
    logger.info(
        "[run_plan] chat_id=%s user_id=%s user_name=%s confirmed=%s original_request=%r",
        chat_id,
        user.get("id"),
        user.get("name"),
        body.confirmed,
        body.original_request[:120] if body.original_request else "<empty>",
    )

    if not body.original_request:
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        logger.warning("[run_plan] REJECT 400: пустой original_request. chat_id=%s user_id=%s payload=%r", chat_id, user.get("id"), payload)
        raise HTTPException(status_code=400, detail="Не указан запрос")

    chat = get_chat(chat_id, user["id"])
    if not chat:
        logger.warning("[run_plan] REJECT 404: чат не найден. chat_id=%s user_id=%s", chat_id, user.get("id"))
        raise HTTPException(status_code=404, detail="Чат не найден")

    source_task = None
    if body.voice_source_task_id is not None:
        source_task = tasks.get(body.voice_source_task_id)
        if (not source_task or source_task.get("user_id") != user["id"]
                or source_task.get("chat_id") != chat_id):
            raise HTTPException(404, detail="Предложение плана не найдено")
        if (not body.confirmed or not source_task.get("plan_suggestion")
                or source_task.get("status") != "done" or source_task.get("plan_declined")
                or source_task.get("voice_plan_started")
                or source_task.get("plan_original_request") != body.original_request):
            raise HTTPException(409, detail="Предложение плана уже закрыто или изменилось")

    plan = get_user_plan(user["id"])
    if not _is_admin(user) and plan not in ("pro", "business"):
        if not body.confirmed:
            logger.warning("[run_plan] REJECT 403: free без confirmed. chat_id=%s user_id=%s plan=%s", chat_id, user.get("id"), plan)
            raise HTTPException(status_code=403, detail="Free: требуется подтверждение")
        trial_used = get_plan_trial_used(user["id"])
        if trial_used:
            logger.warning("[run_plan] REJECT 403: free-trial уже использован. user_id=%s", user["id"])
            raise HTTPException(status_code=403, detail="Режим План доступен на Pro-тарифе. Вы уже использовали пробный запуск.")
        set_plan_trial_used(user["id"], 1)
        logger.info("[run_plan] free-trial использован. user_id=%s", user["id"])

    user_devs = {dk: dev for dk, dev in devices.items() if dev.get("user_id") == user["id"]}
    if not user_devs:
        return {"status": "error", "error": "Нет подключённых устройств"}

    if body.device_id and _dk(user["id"], body.device_id) not in user_devs:
        return {"status":"error","error":"device_not_owned_or_unavailable"}

    if source_task is not None and source_task.get("device_ids"):
        target_ids=list(source_task["device_ids"])
    elif body.device_id:
        target_ids = [_dk(user["id"], body.device_id)]
    else:
        target_ids = [list(user_devs.keys())[0]]

    try:
        task=await submit_worker(user,chat_id,body.original_request,target_ids,{"pipeline":True,"autonomous":False},
            request_key="plan:"+body.voice_source_task_id if body.voice_source_task_id else None,
            objective=body.original_request if source_task and source_task.get("orchestrated") else "",broadcast=bool(source_task and source_task.get("broadcast")),
            source_task_ids=source_task.get("source_task_ids") or [] if source_task else ())
    except ValueError as exc:
        return {"status":"error","error":str(exc)}
    task_id=task["task_id"]
    if source_task is not None:source_task["voice_plan_started"]=task_id
    return {"status":"ok","task_id":task_id,"chat_id":chat_id,"worker_id":"worker-1","worker_status":task["status"]}


@router.get("/api/download/{token}")
async def download_file(token: str, request: Request):
    info = download_tokens.get(token)
    if not info:
        raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")
    if time.time() - info["created"] > TOKEN_TTL:
        download_tokens.pop(token, None)
        raise HTTPException(status_code=410, detail="Ссылка истекла")

    if request.method == "HEAD":
        return StreamingResponse(BytesIO(b""), media_type="application/octet-stream")

    short_did = info["device_id"]
    file_path = info["file_path"]
    dl_user_id = info.get("user_id", 0)
    device_key = _dk(dl_user_id, short_did) if dl_user_id else short_did

    try:
        result = await send_command_to_agent(device_key, "get_file_content", {"path": file_path})
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    if "error" in result and result["error"]:
        error_text = str(result["error"])
        if error_text.startswith("FILE_TOO_LARGE:"):
            return JSONResponse(status_code=413, content={"status": "error", "error": error_text.replace("FILE_TOO_LARGE:", "", 1).strip()})
        return JSONResponse(status_code=400, content={"status": "error", "error": error_text})

    data = base64.b64decode(result["data_b64"])
    filename = result.get("filename", "file")
    return StreamingResponse(
        BytesIO(data),
        media_type="application/octet-stream",
        headers=_build_download_headers(filename),
    )


@router.post("/api/download_request")
async def download_request(body: dict, request: Request):
    user = get_current_user(request)
    device_id = body.get("device_id")
    file_path = body.get("file_path")
    if not device_id or not file_path:
        return {"status": "error", "error": "device_id и file_path обязательны"}

    device_key = device_id if device_id in devices else _dk(user["id"], device_id)
    if device_key not in devices:
        return {"status": "error", "error": "Нет доступа к устройству"}

    token = create_download_token(_short_did(device_key), file_path, user_id=user["id"])
    return {"status": "ok", "url": f"/api/download/{token}"}


@router.post("/api/raw_command")
async def api_raw_command(cmd: RawCommand, request: Request):
    user = get_current_user(request)

    if not _is_admin(user):
        plan = get_user_plan(user["id"])
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        if not limits.get("dev_mode"):
            return {"status": "error", "error": "Режим разработчика доступен только на тарифе Pro или Business."}

    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}
    if not cmd.command.strip():
        return {"status": "error", "error": "Команда не может быть пустой"}

    user_devs = get_user_devices(user["id"])
    if not user_devs:
        return {"status": "error", "error": "Нет подключённых устройств"}

    if cmd.broadcast:
        target_ids = list(user_devs.keys())
    else:
        cmd_dk = _dk(user["id"], cmd.device_id)
        if cmd_dk not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено"}
        target_ids = [cmd_dk]

    raw_cmd = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; {cmd.command}"
    results = {}

    async def exec_on_device(device_id: str):
        try:
            result = await send_command_to_agent(device_id, "execute_cmd", {"command": raw_cmd}, user_id=user["id"])
            results[device_id] = {"status": "ok", "result": result}
        except Exception as exc:
            error_str = str(exc)
            if "CONFIRM_REQUIRED" in error_str:
                results[device_id] = {"status": "confirm_required", "command": cmd.command, "error": error_str}
            elif "BLOCKED" in error_str:
                results[device_id] = {"status": "blocked", "error": error_str}
            else:
                results[device_id] = {"status": "error", "error": error_str}

    await asyncio.gather(*[exec_on_device(device_id) for device_id in target_ids])
    add_audit_log(user["id"], user["name"], "raw_command", f"cmd={cmd.command[:120]} devices={target_ids}", request.client.host if request.client else None)
    if cmd.broadcast:
        for item in results.values():
            payload = item.get("result") or {}
            if isinstance(payload, dict) and (payload.get("error") or payload.get("returncode", 0) not in (0, None)):
                item["status"] = "error"
        successes = sum(item["status"] == "ok" for item in results.values())
        overall = "success" if successes == len(results) else ("partial_failure" if successes else "failed")
        return {"status": "ok" if overall == "success" else "error", "overall_status": overall,
                "results": results, "broadcast": True, "device_count": len(target_ids)}
    return {"status": "ok", "results": results, "broadcast": cmd.broadcast, "device_count": len(target_ids)}

import asyncio
import base64
import logging
import time
import uuid
from io import BytesIO
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

try:
    from ..api_support import _is_admin, check_ip_rate_limit, check_rate_limit, get_current_user
    from ..database import (
        PLAN_LIMITS,
        add_audit_log,
        add_message,
        add_user_fact,
        check_daily_command_limit,
        check_device_limit,
        create_chat,
        delete_memory_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
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
        mark_suggested_fact_declined,
        mark_plan_declined,
        tasks,
        TOKEN_TTL,
    )
    from ..task_runtime import run_nl_task, run_onboarding_task, send_command_to_agent
except ImportError:
    from api_support import _is_admin, check_ip_rate_limit, check_rate_limit, get_current_user
    from database import (
        PLAN_LIMITS,
        add_audit_log,
        add_message,
        add_user_fact,
        check_daily_command_limit,
        check_device_limit,
        create_chat,
        delete_memory_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
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
        mark_suggested_fact_declined,
        mark_plan_declined,
        tasks,
        TOKEN_TTL,
    )
    from task_runtime import run_nl_task, run_onboarding_task, send_command_to_agent


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


class RunPlanBody(BaseModel):
    original_request: str
    device_id: Optional[str] = None
    confirmed: bool = False


class MemoryFactDeleteBody(BaseModel):
    id: int
    source: str
    device_id: str | None = None


def _owned_device_profile(user: dict, device_id: str | None = None, machine_guid: str | None = None) -> dict | None:
    if device_id:
        profile = get_device_profile(_short_did(device_id))
        if profile and (profile.get("user_id") == user["id"] or _is_admin(user)):
            return profile
        return None

    profiles = get_user_device_profiles(user["id"])
    if machine_guid:
        return next((p for p in profiles if p.get("machine_guid") == machine_guid), None)
    return profiles[0] if profiles else None


def _memory_stats_for_profile(user: dict, profile: dict | None) -> dict:
    machine_guid = profile.get("machine_guid") if profile else None
    return get_memory_stats(machine_guid, str(user["id"]) if user.get("id") else None)


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

    if not _is_admin(user):
        cmd_limit = check_daily_command_limit(user["id"])
        if not cmd_limit["allowed"]:
            return {
                "status": "error",
                "error": f"Дневной лимит команд исчерпан ({cmd_limit['used']}/{cmd_limit['limit']}). Обновите тариф для снятия ограничений.",
            }
        increment_daily_commands(user["id"])

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

    add_message(chat_id, "user", cmd.message)

    if not user_devs and not cmd.device_id:
        task_id = str(uuid.uuid4())[:12]
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user["id"],
            "chat_id": chat_id,
            "message": cmd.message,
            "device_ids": [],
            "status": "running",
            "results": {},
            "answer": None,
            "commands": None,
            "created_at": time.time(),
        }
        asyncio.create_task(run_onboarding_task(task_id, user["id"], cmd.message, chat_id))
        return {"status": "ok", "task_id": task_id, "chat_id": chat_id, "device_ids": []}

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

    task_id = str(uuid.uuid4())[:12]
    tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": chat_id,
        "message": cmd.message,
        "device_ids": target_ids,
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "modes": cmd.modes or {},
        "created_at": time.time(),
    }
    asyncio.create_task(run_nl_task(task_id, user["id"], cmd.message, target_ids, chat_id))

    return {"status": "ok", "task_id": task_id, "chat_id": chat_id, "device_ids": target_ids}


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

    ok = delete_memory_fact(str(user["id"]), body.id, source, machine_guid)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory fact not found")

    return {"status": "ok", "memory_stats": _memory_stats_for_profile(user, profile)}


@router.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    memory_stats = None
    if task.get("device_ids"):
        try:
            first_did = _short_did(task["device_ids"][0])
            profile = get_device_profile(first_did)
            if profile and profile.get("machine_guid"):
                memory_stats = get_memory_stats(profile["machine_guid"], str(user["id"]) if user.get("id") else None)
        except Exception:
            pass

    response_task = {
        "task_id": task["task_id"],
        "chat_id": task["chat_id"],
        "message": task["message"],
        "device_ids": task["device_ids"],
        "status": task["status"],
        "answer": task.get("answer"),
        "commands": task.get("commands"),
        "tasks": task.get("tasks", []),
        "current_step": task.get("current_step"),
        "results": task.get("results", {}),
        "confirm_data": task.get("confirm_data"),
        "created_at": task["created_at"],
        "memory_stats": memory_stats,
        "suggested_fact": task.get("suggested_fact"),
    }
    if task.get("plan_suggestion"):
        response_task["plan_suggestion"] = task["plan_suggestion"]
        response_task["plan_original_request"] = task.get("plan_original_request", "")
        user_plan = get_user_plan(user["id"])
        if user_plan == "free":
            response_task["plan_trial_used"] = bool(get_plan_trial_used(user["id"]))
    if task.get("auto_plan"):
        response_task["auto_plan"] = True

    return {"status": "ok", "task": response_task}


@router.post("/api/tasks/{task_id}/confirm")
async def api_confirm_task(task_id: str, request: Request):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "confirm":
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    confirm_data = task.get("confirm_data", {})
    short_did = confirm_data.get("device_id", "")
    params = confirm_data.get("params", {})
    chat_id = confirm_data.get("chat_id", task.get("chat_id"))
    confirm_dk = _dk(task["user_id"], short_did) if ":" not in short_did else short_did
    task["status"] = "running"
    task.pop("confirm_data", None)

    async def execute_confirmed():
        try:
            result = await send_command_to_agent(confirm_dk, "execute_cmd", params, skip_confirm=True)
            cmd_entry = {"command": confirm_data.get("command", ""), "device_id": short_did, "result": result}
            existing_cmds = task.get("commands", []) or []
            existing_cmds.append(cmd_entry)
            task["commands"] = existing_cmds
            ok = not result.get("error")
            task["answer"] = "Выполнено." if ok else f"Ошибка: {result.get('error', '')}"
            task["status"] = "done"
            add_message(chat_id, "assistant", task["answer"], task["commands"])
        except Exception as exc:
            task["status"] = "error"
            task["answer"] = f"Ошибка: {str(exc)}"
            add_message(chat_id, "assistant", task["answer"])

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
    fact_id = add_user_fact(user_id=str(user["id"]), text=suggested_fact["text"], category=suggested_fact.get("category"))
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

    chat_id = task.get("confirm_data", {}).get("chat_id", task.get("chat_id"))
    task["status"] = "done"
    task["answer"] = "Команда отменена пользователем."
    task.pop("confirm_data", None)
    add_message(chat_id, "assistant", task["answer"], task.get("commands", []))
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

    plan = get_user_plan(user["id"])
    if plan not in ("pro", "business"):
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

    if body.device_id:
        target_ids = [_dk(user["id"], body.device_id)]
    else:
        target_ids = [list(user_devs.keys())[0]]

    task_id = str(uuid.uuid4())[:12]
    tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": chat_id,
        "message": body.original_request,
        "device_ids": target_ids,
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "modes": {"pipeline": True, "autonomous": True},
        "created_at": time.time(),
    }
    asyncio.create_task(run_nl_task(task_id, user["id"], body.original_request, target_ids, chat_id))
    return {"status": "ok", "task_id": task_id, "chat_id": chat_id}


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
    return {"status": "ok", "results": results, "broadcast": cmd.broadcast, "device_count": len(target_ids)}

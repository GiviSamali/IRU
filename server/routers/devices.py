from fastapi import APIRouter, HTTPException, Request

try:
    from ..api_support import _is_admin, get_current_user
    from ..database import get_device_profile, get_user_device_profiles, update_device_activation_summary, update_device_python_runtime_summary
    from ..device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
        validate_activation_receipt,
    )
    from ..runtime_state import _dk, _short_did, devices, get_user_devices
    from ..task_runtime import collect_device_live_snapshot, compact_state_snapshot_summary, send_command_to_agent
    from ..tool_registry import tool_log_entry
    from ..python_runtime import compact_python_runtime_summary, parse_python_runtime_summary, python_runtime_status_from_summary, validate_python_runtime_receipt
except ImportError:
    from api_support import _is_admin, get_current_user
    from database import get_device_profile, get_user_device_profiles, update_device_activation_summary, update_device_python_runtime_summary
    from device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
        validate_activation_receipt,
    )
    from runtime_state import _dk, _short_did, devices, get_user_devices
    from task_runtime import collect_device_live_snapshot, compact_state_snapshot_summary, send_command_to_agent
    from tool_registry import tool_log_entry
    from python_runtime import compact_python_runtime_summary, parse_python_runtime_summary, python_runtime_status_from_summary, validate_python_runtime_receipt


router = APIRouter()

ACTIVATION_MODES = {"soft", "full", "repair"}


def _runtime_device_key_for_user(user: dict, device_id: str) -> str | None:
    if _is_admin(user):
        for key, dev in devices.items():
            if _short_did(key) == device_id or dev.get("short_device_id") == device_id:
                return key
        return None
    key = _dk(user["id"], device_id)
    return key if key in devices else None


def _ensure_device_access(user: dict, device_id: str) -> None:
    profile = get_device_profile(device_id)
    if profile:
        if not _is_admin(user) and profile.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="Нет доступа к устройству")
        return
    if _runtime_device_key_for_user(user, device_id):
        return
    raise HTTPException(status_code=404, detail=f"Устройство '{device_id}' не найдено")


def _device_api_item(short_did: str, dev: dict, profile: dict | None) -> dict:
    summary = dev.get("activation_summary") or parse_activation_summary((profile or {}).get("activation_summary"))
    runtime_summary = dev.get("python_runtime_summary") or parse_python_runtime_summary((profile or {}).get("python_runtime_summary"))
    state_summary = compact_state_snapshot_summary(dev.get("last_state_snapshot"))
    runtime_status = python_runtime_status_from_summary(runtime_summary)
    activation_runtime_status = runtime_status_from_summary(summary)
    caps = (summary.get("capabilities_summary") if isinstance(summary, dict) else None) or {}
    if runtime_status == "ok":
        caps = dict(caps) if isinstance(caps, dict) else {str(item): "available" for item in caps}
        caps["python"] = "available"
    return {
        "device_id": short_did,
        "info": dev.get("info", {}),
        "connected": bool(dev.get("ws")),
        "activation_status": activation_status_from_summary(summary),
        "runtime_status": runtime_status if runtime_status != "unknown" else activation_runtime_status,
        "python_runtime_status": runtime_status,
        "python_version": runtime_summary.get("python_version"),
        "pip_status": runtime_summary.get("pip_status"),
        "last_runtime_check": runtime_summary.get("last_runtime_check"),
        "venv_python": runtime_summary.get("venv_python"),
        "health_status": state_summary.get("health_status"),
        "last_snapshot_at": state_summary.get("last_snapshot_at"),
        "identity_status": state_summary.get("identity_status"),
        "cpu_load": state_summary.get("cpu_load"),
        "ram_used_pct": state_summary.get("ram_used_pct"),
        "disk_used_pct": state_summary.get("disk_used_pct"),
        "process_count": state_summary.get("process_count"),
        "uptime": state_summary.get("uptime"),
        "capabilities_summary": caps,
    }


async def prepare_runtime_for_user(user: dict, device_id: str, mode: str = "check", packages: list | None = None) -> dict:
    mode = (mode or "check").strip().lower()
    if mode not in {"check", "prepare", "repair"}:
        raise HTTPException(status_code=422, detail="mode must be one of: check, prepare, repair")
    device_id = _short_did(device_id)
    _ensure_device_access(user, device_id)
    device_key = _runtime_device_key_for_user(user, device_id)
    dev = devices.get(device_key or "")
    if not dev or not dev.get("ws"):
        raise HTTPException(status_code=503, detail=f"Agent for device '{device_id}' is offline")
    try:
        receipt = await send_command_to_agent(
            device_key,
            "device.prepare_runtime",
            {
                "mode": mode,
                "packages": packages or [],
                "python_version_policy": "existing",
                "device_id": device_id,
            },
            user_id=user["id"],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not isinstance(receipt, dict):
        raise HTTPException(status_code=409, detail="Missing Python runtime receipt")
    if receipt.get("error"):
        error = str(receipt.get("error") or "")
        if "unknown" in error.lower() or "неизвест" in error.lower():
            raise HTTPException(status_code=501, detail="device.prepare_runtime is not implemented by this agent")
        if mode == "prepare" and "AGENT_DISCONNECTED" in error:
            raise HTTPException(
                status_code=409,
                detail=(
                    "runtime_prepare_interrupted: Подготовка прервана: агент отключился. "
                    "Повторите check после переподключения."
                ),
            )
        raise HTTPException(status_code=409, detail=f"Python runtime preparation failed: {error}")
    valid, reason = validate_python_runtime_receipt(receipt)
    if not valid:
        raise HTTPException(status_code=409, detail=f"Invalid Python runtime receipt: {reason}")
    summary = compact_python_runtime_summary(receipt)
    dev["python_runtime_receipt"] = receipt
    dev["python_runtime_summary"] = summary
    update_device_python_runtime_summary(device_id, summary)
    tool_name = {
        "check": "device_check_runtime",
        "prepare": "device_prepare_runtime",
        "repair": "device_repair_runtime",
    }[mode]
    return {
        "status": summary.get("runtime_status"),
        "device_id": device_id,
        "summary": summary,
        "receipt": receipt,
        "tool_log": tool_log_entry(
            tool_name,
            {"status": summary.get("runtime_status"), "runtime_summary": summary},
            target_device_id=device_id,
            hostname=(dev.get("info") or {}).get("hostname") or device_id,
        ),
    }


async def activate_device_for_user(user: dict, device_id: str, mode: str = "soft") -> dict:
    mode = (mode or "soft").strip().lower()
    if mode not in ACTIVATION_MODES:
        raise HTTPException(status_code=422, detail="mode must be one of: soft, full, repair")
    device_id = _short_did(device_id)
    _ensure_device_access(user, device_id)
    device_key = _runtime_device_key_for_user(user, device_id)
    dev = devices.get(device_key or "")
    if not dev or not dev.get("ws"):
        raise HTTPException(status_code=503, detail=f"Agent for device '{device_id}' is offline")
    try:
        receipt = await send_command_to_agent(device_key, "device.activate", {"mode": mode, "device_id": device_id}, user_id=user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not isinstance(receipt, dict) or receipt.get("error"):
        error = receipt.get("error") if isinstance(receipt, dict) else "missing activation receipt"
        raise HTTPException(status_code=409, detail=f"Device activation failed: {error}")
    valid, reason = validate_activation_receipt(receipt)
    if not valid:
        raise HTTPException(status_code=409, detail=f"Invalid activation receipt: {reason}")
    summary = compact_activation_summary(receipt)
    dev["activation_receipt"] = receipt
    dev["activation_summary"] = summary
    update_device_activation_summary(device_id, summary)
    return {
        "status": "ok",
        "receipt": receipt,
        "summary": summary,
        "tool_log": tool_log_entry(
            "device_repair_activation" if mode == "repair" else "device_activate",
            {"status": "ok", "activation_summary": summary},
            target_device_id=device_id,
            hostname=(dev.get("info") or {}).get("hostname") or device_id,
        ),
    }


@router.get("/api/devices")
async def get_devices_api(request: Request):
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    print(f"[api/devices] user_id={user['id']}, user_devs={list(user_devs.keys())}")
    result = {}
    for composite_key, dev in user_devs.items():
        short_did = dev.get("short_device_id", _short_did(composite_key))
        profile = get_device_profile(short_did)
        result[short_did] = _device_api_item(short_did, dev, profile)
    return {"devices": result}


@router.post("/api/devices/{device_id}/activate")
async def api_activate_device(device_id: str, request: Request):
    user = get_current_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await activate_device_for_user(user, device_id, (body or {}).get("mode", "soft"))


@router.post("/api/devices/{device_id}/state")
async def api_device_state(device_id: str, request: Request):
    user = get_current_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = (body or {}).get("mode", "snapshot")
    if mode != "snapshot":
        raise HTTPException(status_code=422, detail="mode must be snapshot")
    device_id = _short_did(device_id)
    _ensure_device_access(user, device_id)
    device_key = _runtime_device_key_for_user(user, device_id)
    if not device_key:
        raise HTTPException(status_code=503, detail=f"Agent for device '{device_id}' is offline")
    result = await collect_device_live_snapshot(device_key, user_id=user["id"])
    return {
        "status": result.get("status"),
        "device_id": device_id,
        "snapshot": result.get("snapshot"),
        "identity_receipt": result.get("identity_receipt"),
        "health_summary": result.get("health_summary"),
        "last_state_snapshot": devices.get(device_key, {}).get("last_state_snapshot"),
        "tool_log": tool_log_entry(
            "device_refresh_state",
            result,
            target_device_id=device_id,
            hostname=(devices.get(device_key, {}).get("info") or {}).get("hostname") or device_id,
        ),
    }


@router.post("/api/devices/{device_id}/runtime")
async def api_device_runtime(device_id: str, request: Request):
    user = get_current_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await prepare_runtime_for_user(
        user,
        device_id,
        (body or {}).get("mode", "check"),
        (body or {}).get("packages") or [],
    )


@router.post("/api/devices/{device_id}/disconnect")
async def api_disconnect_device(device_id: str, request: Request):
    user = get_current_user(request)
    device_id = _short_did(device_id)
    _ensure_device_access(user, device_id)
    device_key = _runtime_device_key_for_user(user, device_id)
    if not device_key:
        raise HTTPException(status_code=503, detail=f"Agent for device '{device_id}' is offline")
    result = await send_command_to_agent(device_key, "agent.disconnect", {}, user_id=user["id"])
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(status_code=501, detail="agent.disconnect is not implemented by this agent")
    return {"status": "ok", "device_id": device_id, "ack": result}


@router.post("/api/devices/{device_id}/shutdown")
async def api_shutdown_device(device_id: str, request: Request):
    user = get_current_user(request)
    device_id = _short_did(device_id)
    _ensure_device_access(user, device_id)
    device_key = _runtime_device_key_for_user(user, device_id)
    if not device_key:
        raise HTTPException(status_code=503, detail=f"Agent for device '{device_id}' is offline")
    result = await send_command_to_agent(device_key, "agent.shutdown", {}, user_id=user["id"])
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(status_code=501, detail="agent.shutdown is not implemented by this agent")
    return {"status": "ok", "device_id": device_id, "ack": result}


@router.get("/api/device_profiles")
async def api_device_profiles(request: Request):
    user = get_current_user(request)
    if _is_admin(user):
        try:
            from ..database import get_db
        except ImportError:
            from database import get_db

        with get_db() as conn:
            rows = conn.execute("SELECT * FROM device_profiles ORDER BY updated_at DESC").fetchall()
            profiles = []
            for row in rows:
                item = dict(row)
                if item.get("disks"):
                    try:
                        item["disks"] = __import__("json").loads(item["disks"])
                    except Exception:
                        pass
                profiles.append(item)
    else:
        profiles = get_user_device_profiles(user["id"])
    for profile in profiles:
        summary = parse_activation_summary(profile.get("activation_summary"))
        runtime_summary = parse_python_runtime_summary(profile.get("python_runtime_summary"))
        profile["activation_status"] = activation_status_from_summary(summary)
        profile["runtime_status"] = python_runtime_status_from_summary(runtime_summary) if runtime_summary else runtime_status_from_summary(summary)
        profile["python_runtime_status"] = python_runtime_status_from_summary(runtime_summary)
    return {"status": "ok", "profiles": profiles}


@router.get("/api/device_profiles/{device_id}")
async def api_device_profile(device_id: str, request: Request):
    user = get_current_user(request)
    profile = get_device_profile(device_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Профиль устройства не найден")
    if not _is_admin(user) and profile.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Нет доступа")
    summary = parse_activation_summary(profile.get("activation_summary"))
    runtime_summary = parse_python_runtime_summary(profile.get("python_runtime_summary"))
    profile["activation_status"] = activation_status_from_summary(summary)
    profile["runtime_status"] = python_runtime_status_from_summary(runtime_summary) if runtime_summary else runtime_status_from_summary(summary)
    profile["python_runtime_status"] = python_runtime_status_from_summary(runtime_summary)
    return {"status": "ok", "profile": profile}

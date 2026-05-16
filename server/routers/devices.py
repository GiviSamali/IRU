from fastapi import APIRouter, HTTPException, Request

try:
    from ..api_support import _is_admin, get_current_user
    from ..database import get_device_profile, get_user_device_profiles, update_device_activation_summary
    from ..device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
        validate_activation_receipt,
    )
    from ..runtime_state import _dk, _short_did, devices, get_user_devices
    from ..task_runtime import send_command_to_agent
except ImportError:
    from api_support import _is_admin, get_current_user
    from database import get_device_profile, get_user_device_profiles, update_device_activation_summary
    from device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
        validate_activation_receipt,
    )
    from runtime_state import _dk, _short_did, devices, get_user_devices
    from task_runtime import send_command_to_agent


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
    return {"status": "ok", "receipt": receipt, "summary": summary}


@router.get("/api/devices")
async def get_devices_api(request: Request):
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    print(f"[api/devices] user_id={user['id']}, user_devs={list(user_devs.keys())}")
    result = {}
    for composite_key, dev in user_devs.items():
        short_did = dev.get("short_device_id", _short_did(composite_key))
        profile = get_device_profile(short_did)
        summary = dev.get("activation_summary") or parse_activation_summary((profile or {}).get("activation_summary"))
        result[short_did] = {
            "device_id": short_did,
            "info": dev.get("info", {}),
            "connected": True,
            "activation_status": activation_status_from_summary(summary),
            "runtime_status": runtime_status_from_summary(summary),
        }
    return {"devices": result}


@router.post("/api/devices/{device_id}/activate")
async def api_activate_device(device_id: str, request: Request):
    user = get_current_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await activate_device_for_user(user, device_id, (body or {}).get("mode", "soft"))


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
        profile["activation_status"] = activation_status_from_summary(summary)
        profile["runtime_status"] = runtime_status_from_summary(summary)
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
    profile["activation_status"] = activation_status_from_summary(summary)
    profile["runtime_status"] = runtime_status_from_summary(summary)
    return {"status": "ok", "profile": profile}

from fastapi import APIRouter, HTTPException, Request

try:
    from ..api_support import _is_admin, get_current_user
    from ..database import get_device_profile, get_user_device_profiles
    from ..runtime_state import _short_did, get_user_devices
except ImportError:
    from api_support import _is_admin, get_current_user
    from database import get_device_profile, get_user_device_profiles
    from runtime_state import _short_did, get_user_devices


router = APIRouter()


@router.get("/api/devices")
async def get_devices_api(request: Request):
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    print(f"[api/devices] user_id={user['id']}, user_devs={list(user_devs.keys())}")
    result = {}
    for composite_key, dev in user_devs.items():
        short_did = dev.get("short_device_id", _short_did(composite_key))
        result[short_did] = {
            "device_id": short_did,
            "info": dev.get("info", {}),
            "connected": True,
        }
    return {"devices": result}


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
    return {"status": "ok", "profiles": profiles}


@router.get("/api/device_profiles/{device_id}")
async def api_device_profile(device_id: str, request: Request):
    user = get_current_user(request)
    profile = get_device_profile(device_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Профиль устройства не найден")
    if not _is_admin(user) and profile.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Нет доступа")
    return {"status": "ok", "profile": profile}

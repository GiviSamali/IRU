import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

try:
    from ..api_support import _is_admin
    from ..database import add_audit_log, check_device_limit, get_user_by_token, upsert_device_profile
    from ..runtime_state import _dk, devices, get_user_devices
except ImportError:
    from api_support import _is_admin
    from database import add_audit_log, check_device_limit, get_user_by_token, upsert_device_profile
    from runtime_state import _dk, devices, get_user_devices


router = APIRouter()


def _fail_pending_commands(connection: dict, device_id: str) -> None:
    pending = connection.get("pending", {})
    for future in list(pending.values()):
        if future is not None and not future.done():
            future.set_result({"error": f"AGENT_DISCONNECTED: устройство '{device_id}' отключилось во время выполнения команды"})
    pending.clear()


@router.websocket("/ws/{device_id}")
async def websocket_agent(ws: WebSocket, device_id: str, user_token: str = Query(default="")):
    user = get_user_by_token(user_token) if user_token else None
    if not user:
        await ws.close(code=4001, reason="Недействительный токен пользователя")
        return

    composite_key = _dk(user["id"], device_id)
    if not _is_admin(user):
        current_count = len(get_user_devices(user["id"]))
        dev_limit = check_device_limit(user["id"], current_count)
        if not dev_limit["allowed"] and composite_key not in devices:
            await ws.close(code=4003, reason=f"Лимит устройств исчерпан ({dev_limit['current']}/{dev_limit['limit']})")
            return

    await ws.accept()
    print(f"[ws] agent connected: device_id='{device_id}', user_id={user['id']}, user='{user['name']}'")
    add_audit_log(user["id"], user["name"], "agent_connect", f"device={device_id}", None)

    old = devices.get(composite_key)
    connection = {
        "ws": ws,
        "info": {},
        "registered_identity": {"target_device_id": device_id, "registered_hostname": None, "registered_machine_guid": None},
        "pending": {},
        "user_id": user["id"],
        "short_device_id": device_id,
    }
    devices[composite_key] = connection
    if old:
        _fail_pending_commands(old, device_id)
    print(f"[ws] devices after connect: {list(devices.keys())}")

    try:
        if old:
            try:
                await asyncio.wait_for(old["ws"].close(code=4000, reason="agent_reconnected"), timeout=2)
            except Exception:
                pass
        while True:
            raw = await ws.receive_text()
            if devices.get(composite_key) is not connection:
                break
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "register":
                payload = data.get("payload", {})
                connection["info"] = payload
                cached_passport = payload.get("cached_passport") if isinstance(payload.get("cached_passport"), dict) else {}
                if cached_passport:
                    connection["agent_cached_passport"] = cached_passport
                activation_summary = payload.get("activation_summary") or cached_passport.get("activation_summary")
                runtime_summary = payload.get("runtime_summary") or cached_passport.get("runtime_summary")
                state_summary = payload.get("state_snapshot_summary") or cached_passport.get("state_snapshot_summary")
                state_record = cached_passport.get("state_snapshot") if isinstance(cached_passport.get("state_snapshot"), dict) else None
                if isinstance(activation_summary, dict) and activation_summary:
                    connection["activation_summary"] = activation_summary
                if isinstance(runtime_summary, dict) and runtime_summary:
                    connection["python_runtime_summary"] = runtime_summary
                if isinstance(state_record, dict) and state_record:
                    connection["agent_cached_state_snapshot"] = state_record
                elif isinstance(state_summary, dict) and state_summary:
                    connection["last_state_snapshot_summary"] = state_summary
                if isinstance(payload.get("hardware_summary"), dict):
                    connection["hardware_summary"] = payload.get("hardware_summary")
                connection["registered_identity"] = {
                    "target_device_id": device_id,
                    "registered_hostname": payload.get("hostname"),
                    "registered_machine_guid": payload.get("machine_guid"),
                    "machine_guid_type": payload.get("machine_guid_type"),
                }
                try:
                    upsert_device_profile(device_id, user["id"], payload)
                    print(f"[ws] device profile saved: {device_id}")
                except Exception as exc:
                    print(f"[ws] failed to save device profile: {exc}")
                print(f"[ws] registered: {device_id} — {data.get('payload', {})}")

            elif msg_type == "result":
                payload = data.get("payload", {})
                cmd_id = payload.get("id")
                future = connection["pending"].pop(cmd_id, None)
                if future and not future.done():
                    if payload.get("status") == "ok":
                        future.set_result(payload.get("result", {}))
                    else:
                        future.set_result({"error": payload.get("error", "Неизвестная ошибка")})

    except WebSocketDisconnect:
        print(f"[ws] agent disconnected: device_id='{device_id}', user_id={user['id']}")
    except Exception as exc:
        print(f"[ws] error for {device_id}: {exc}")
    finally:
        _fail_pending_commands(connection, device_id)
        if devices.get(composite_key) is connection:
            devices.pop(composite_key, None)
            print(f"[ws] device removed: {device_id}")
        else:
            print(f"[ws] device already reconnected, keeping: {device_id}")
        print(f"[ws] devices after disconnect: {list(devices.keys())}")
        add_audit_log(user["id"], user["name"], "agent_disconnect", f"device={device_id}", None)

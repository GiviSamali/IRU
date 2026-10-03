"""Authenticated pairing and separate Browser Bridge WebSocket."""
import asyncio
import json
import re
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
try:
    from ..api_support import get_current_user
    from ..browser_bridge import (BrowserConnection, MAX_MESSAGE_BYTES, authenticate_credential, authenticate_credential_hash,
        bridges, disconnect_bridge, issue_pairing, receive_result, require_owned_device)
    from ..runtime_state import _dk
except ImportError:
    from api_support import get_current_user
    from browser_bridge import (BrowserConnection, MAX_MESSAGE_BYTES, authenticate_credential, authenticate_credential_hash,
        bridges, disconnect_bridge, issue_pairing, receive_result, require_owned_device)
    from runtime_state import _dk

router = APIRouter()
_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")


def _cors_headers(request):
    origin = request.headers.get("origin")
    if origin is None:
        return {}
    if not _EXTENSION_ORIGIN.fullmatch(origin):
        raise HTTPException(403, "invalid_browser_origin")
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}


@router.options("/api/browser/pair")
async def pair_preflight(request: Request):
    headers = _cors_headers(request)
    requested = {value.strip().lower() for value in request.headers.get("access-control-request-headers", "").split(",") if value.strip()}
    if request.headers.get("access-control-request-method") != "POST" or not requested <= {"authorization", "x-token", "content-type"}:
        raise HTTPException(403, "invalid_browser_preflight")
    return JSONResponse({}, headers={**headers, "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "Authorization, X-Token, Content-Type", "Access-Control-Max-Age": "600"})


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str


@router.post("/api/browser/pair")
async def pair_browser(body: PairRequest, request: Request):
    headers = _cors_headers(request)
    try:
        user = get_current_user(request)
        return JSONResponse(issue_pairing(user["id"], body.device_id), headers=headers)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403, headers=headers)


@router.get("/api/browser/status")
async def browser_status(request: Request, device_id: str):
    headers = _cors_headers(request)
    try:
        user = get_current_user(request)
        require_owned_device(user["id"], device_id)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403, headers=headers)
    connection = bridges.get(_dk(user["id"], device_id))
    return JSONResponse({"status": "connected" if connection else "offline", "device_id": device_id,
            "bridge_id": connection.bridge_id if connection else None}, headers=headers)


@router.websocket("/ws/browser")
async def browser_socket(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    if not _EXTENSION_ORIGIN.fullmatch(origin):
        await ws.close(code=4003, reason="invalid_browser_origin")
        return
    await ws.accept()
    connection = None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        if len(raw.encode())>4096:
            raise ValueError("invalid_browser_hello")
        hello = json.loads(raw)
        if not isinstance(hello, dict) or set(hello)!={"type", "token", "device_id", "bridge_id"} or hello.get("type")!="hello":
            raise ValueError("invalid_browser_hello")
        bridge_id = hello["bridge_id"]
        if not isinstance(bridge_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", bridge_id):
            raise ValueError("invalid_bridge_identity")
        credential = authenticate_credential(hello["token"], hello["device_id"])
        owner = credential["owner_user_id"]
        key = _dk(owner, hello["device_id"])
        old = bridges.get(key)
        if old and old.bridge_id != bridge_id:
            try:
                authenticate_credential_hash(old)
            except ValueError:
                # An expired/revoked client cannot retain the device's browser slot.
                pass
            else:
                await ws.close(code=4009, reason="browser_already_connected")
                return
        connection = BrowserConnection(ws, owner, hello["device_id"], bridge_id, credential["credential_hash"], credential["expires_at"])
        bridges[key] = connection
        if old:
            disconnect_bridge(old)
            try:
                await asyncio.wait_for(old.ws.close(code=4000, reason="browser_reconnected"), timeout=2)
            except (asyncio.TimeoutError, RuntimeError, WebSocketDisconnect):
                pass
        await ws.send_json({"type": "ready", "connection_id": connection.connection_id, "device_id": connection.device_id})
        while True:
            raw = await ws.receive_text()
            if len(raw.encode())>MAX_MESSAGE_BYTES:
                raise ValueError("browser_message_too_large")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("malformed_browser_message")
            if message.get("type")=="ping" and set(message)=={"type"}:
                await ws.send_json({"type": "pong"})
            elif message.get("type")=="result":
                receive_result(connection, message)
            else:
                raise ValueError("unsupported_browser_message")
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError, asyncio.TimeoutError):
        await ws.close(code=4003, reason="invalid_browser_protocol_or_credentials")
    finally:
        if connection:
            disconnect_bridge(connection)

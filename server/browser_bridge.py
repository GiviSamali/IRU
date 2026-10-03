"""Owner-scoped Chromium bridge transport. Page content is untrusted data."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from . import database as db
    from .runtime_state import _dk, devices
except ImportError:
    import database as db
    from runtime_state import _dk, devices

OPERATIONS = frozenset({"web.tabs", "web.read", "web.elements", "web.fill", "web.activate", "web.wait", "web.focus"})
MAX_MESSAGE_BYTES = 128 * 1024
MAX_TEXT_CHARS = 24000
PAIR_TTL = 30 * 86400
RECEIPT_TTL = 7 * 86400
RESPONSE_TIMEOUT = 20.0
logger = logging.getLogger(__name__)


@dataclass
class BrowserConnection:
    ws: Any
    owner: int
    device_id: str
    bridge_id: str
    credential_hash: str
    expires_at: float
    connection_id: str = field(default_factory=lambda: secrets.token_hex(16))
    pending: dict = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


bridges: dict[str, BrowserConnection] = {}


def failure(reason: str, *, unknown: bool = False) -> dict:
    result = {"status": "unknown" if unknown else "failed", "error": reason}
    if unknown:
        result["needs_verification"] = True
    return result


def init_browser_bridge(restart: bool = False) -> None:
    with db.get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS browser_credentials (
            credential_hash TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,
            device_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS browser_effects (
            operation_key TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,
            device_id TEXT NOT NULL, request_id TEXT NOT NULL, created_at REAL NOT NULL,
            status TEXT NOT NULL, result_json TEXT, task_id TEXT NOT NULL DEFAULT '')""")
        if "task_id" not in {row[1] for row in conn.execute("PRAGMA table_info(browser_effects)")}:
            conn.execute("ALTER TABLE browser_effects ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS browser_effect_task ON browser_effects(owner_user_id,device_id,task_id,status)")
        if restart:
            conn.execute("UPDATE browser_effects SET status='unknown', result_json=? WHERE status='pending'",
                         (json.dumps(failure("server_restarted", unknown=True)),))
        conn.execute("DELETE FROM browser_credentials WHERE expires_at<=?", (time.time(),))
        conn.execute("DELETE FROM browser_effects WHERE created_at<? AND status!='pending'", (time.time()-RECEIPT_TTL,))


def require_owned_device(owner: int, device_id: str) -> dict:
    if not isinstance(device_id, str) or not device_id or len(device_id) > 128 or ":" in device_id or any(ord(c) < 32 for c in device_id):
        raise ValueError("target_device_not_found")
    dev = devices.get(_dk(owner, device_id))
    profile = db.get_device_profile(device_id, user_id=owner)
    if not profile or profile.get("user_id") != owner or not dev or dev.get("user_id") != owner or not dev.get("ws"):
        raise ValueError("target_device_not_found_or_offline")
    return dev


def issue_pairing(owner: int, device_id: str) -> dict:
    require_owned_device(owner, device_id)
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with db.get_db() as conn:
        conn.execute("INSERT INTO browser_credentials VALUES (?,?,?,?,?)", (digest, owner, device_id, now, now+PAIR_TTL))
    return {"status": "ok", "token": token, "device_id": device_id, "expires_at": now+PAIR_TTL}


def authenticate_credential(token: str, device_id: str) -> dict:
    if not isinstance(token, str) or not 32 <= len(token) <= 256:
        raise ValueError("invalid_browser_credential")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM browser_credentials WHERE credential_hash=?", (digest,)).fetchone()
    if not row or row["expires_at"] <= time.time() or row["device_id"] != device_id:
        raise ValueError("invalid_browser_credential")
    require_owned_device(row["owner_user_id"], device_id)
    return dict(row)


def validate_params(operation: str, params: dict) -> dict:
    if not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(params, dict):
        raise ValueError("unsupported_browser_operation")
    permitted = {
        "web.tabs": set(),
        "web.focus": {"tab_id"},
        "web.read": {"tab_id", "document_id", "revision", "max_chars", "scope", "position"},
        "web.elements": {"tab_id", "document_id", "revision", "max_elements", "position"},
        "web.fill": {"tab_id", "document_id", "revision", "element_id", "text"},
        "web.activate": {"tab_id", "document_id", "revision", "element_id"},
        "web.wait": {"tab_id", "document_id", "revision", "timeout_ms"},
    }[operation]
    # Read actions reject unknown arguments too: there is no arbitrary JS surface.
    if set(params) - permitted:
        raise ValueError("unknown_browser_arguments")
    clean = dict(params)
    bounds = {"tab_id": (0, 2**31-1), "max_chars": (1, MAX_TEXT_CHARS), "max_elements": (1, 200), "timeout_ms": (1, 15000)}
    for name, (low, high) in bounds.items():
        if name in clean and (type(clean[name]) is not int or not low <= clean[name] <= high):
            raise ValueError("invalid_browser_argument:"+name)
    for name in ("document_id", "revision", "element_id"):
        if name in clean and (not isinstance(clean[name], str) or not 1 <= len(clean[name]) <= 128 or any(ord(c)<32 for c in clean[name])):
            raise ValueError("invalid_browser_argument:"+name)
    if "scope" in clean and (not isinstance(clean["scope"], str) or clean["scope"] not in {"main", "page"}):
        raise ValueError("invalid_browser_argument:scope")
    if "position" in clean and (not isinstance(clean["position"], str) or clean["position"] not in {"head", "tail"}):
        raise ValueError("invalid_browser_argument:position")
    if "text" in clean and (not isinstance(clean["text"], str) or len(clean["text"]) > MAX_TEXT_CHARS or "\x00" in clean["text"]):
        raise ValueError("invalid_browser_argument:text")
    if operation == "web.focus" and "tab_id" not in clean:
        raise ValueError("missing_browser_arguments")
    required = {"tab_id", "document_id", "revision", "element_id"} if operation in {"web.fill", "web.activate"} else set()
    if operation == "web.fill":
        required.add("text")
    if operation == "web.wait":
        required.update({"tab_id", "document_id", "revision"})
    if not required <= clean.keys():
        raise ValueError("missing_browser_arguments")
    return clean


def _safe_result(result: Any, operation: str, params: dict) -> dict:
    if not isinstance(result, dict) or result.get("status") not in {"success", "failed", "unknown"}:
        raise ValueError("malformed_browser_result")
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("browser_result_too_large")
    if result.get("error") is not None and (not isinstance(result["error"], str) or len(result["error"])>500):
        raise ValueError("malformed_browser_result")
    if result.get("status") == "success":
        if params.get("tab_id") is not None and result.get("tab_id") != params["tab_id"]:
            raise ValueError("browser_tab_mismatch")
        if operation == "web.focus" and result.get("focused") is not True:
            raise ValueError("browser_focus_not_verified")
        if operation == "web.tabs":
            tabs = result.get("tabs")
            if not isinstance(tabs, list) or len(tabs) > 100 or any(not isinstance(t, dict) or type(t.get("tab_id")) is not int for t in tabs):
                raise ValueError("malformed_browser_result")
        elif operation in {"web.read", "web.elements", "web.fill", "web.activate", "web.wait"}:
            page = result.get("page")
            if not isinstance(page, dict) or not all(isinstance(page.get(k), str) and 0<len(page[k])<=128 for k in ("document_id", "revision")):
                raise ValueError("malformed_browser_result")
            if operation != "web.wait" and params.get("document_id") and page["document_id"] != params["document_id"]:
                raise ValueError("browser_document_mismatch")
            for identity in ("document_id", "revision"):
                if identity in result and result[identity] != page[identity]:
                    raise ValueError("browser_page_identity_mismatch")
        if operation == "web.elements":
            elements = result.get("elements")
            if not isinstance(elements, list) or len(elements)>params.get("max_elements", 200) or any(not isinstance(e, dict) or not isinstance(e.get("element_id"), str) for e in elements):
                raise ValueError("malformed_browser_result")
        if operation == "web.read":
            content = result.get("text", result.get("content"))
            if not isinstance(content, (str, list)):
                raise ValueError("malformed_browser_result")
            if "text" in result and not isinstance(result["text"], str):
                raise ValueError("malformed_browser_result")
            def text_chars(value):
                if isinstance(value, str):
                    return len(value)
                if isinstance(value, list):
                    return sum(text_chars(item) for item in value)
                if isinstance(value, dict):
                    return text_chars(value.get("text", ""))
                return 0
            for key in ("text", "content"):
                if key in result and text_chars(result[key])>params.get("max_chars", MAX_TEXT_CHARS):
                    raise ValueError("browser_result_too_large")
    # Extension cannot inject authority via flags; provenance is stamped server-side.
    clean = dict(result)
    for key in ("authorization", "authorized", "user_id", "owner_user_id", "device_id", "trust", "source",
                "terminal_sufficient", "completion_state", "success_criteria", "confirmed", "confirmation",
                "confirmation_required", "tool_calls", "commands"):
        clean.pop(key, None)
    clean["trust"] = "untrusted_page_data"
    clean["source"] = "browser_bridge"
    if clean["status"] == "unknown":
        clean["needs_verification"] = True
    return clean


def _effect_key(owner: int, task_id: str, device_id: str, operation: str, params: dict) -> str:
    # One activation slot per task/tab/document; refreshed DOM IDs cannot resend.
    identity = [owner, task_id, device_id, operation, params.get("tab_id"), params.get("document_id")]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _reserve_effect(owner: int, task_id: str, device_id: str, operation: str, params: dict) -> tuple[str, str, dict | None]:
    key = _effect_key(owner, task_id, device_id, operation, params)
    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM browser_effects WHERE operation_key=?", (key,)).fetchone()
        if row:
            result = json.loads(row["result_json"]) if row["result_json"] else failure("browser_action_in_progress", unknown=True)
            # The static extension reports stale_element BEFORE DOM activation.
            # A fresh observation may safely retry that rejected attempt; success,
            # pending and unknown outcomes remain permanently guarded for this run.
            if not (row["status"] == "failed" and result.get("error") == "stale_element"):
                return key, row["request_id"], {**result, "deduplicated": True}
        uncertain = conn.execute("SELECT request_id FROM browser_effects WHERE owner_user_id=? AND device_id=? AND task_id=? AND status IN ('pending','unknown') LIMIT 1", (owner, device_id, task_id)).fetchone()
        if uncertain:
            return key, uncertain["request_id"], {**failure("prior_browser_action_needs_verification", unknown=True), "deduplicated": True}
        request_id = secrets.token_hex(16)
        if row:
            conn.execute("UPDATE browser_effects SET request_id=?,status='pending',result_json=NULL WHERE operation_key=?", (request_id, key))
        else:
            conn.execute("INSERT INTO browser_effects (operation_key,owner_user_id,device_id,request_id,created_at,status,result_json,task_id) VALUES (?,?,?,?,?,?,?,?)", (key, owner, device_id, request_id, time.time(), "pending", None, task_id))
    return key, request_id, None


def _finish_effect(key: str, result: dict) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE browser_effects SET status=?,result_json=? WHERE operation_key=?", (result["status"], json.dumps(result, ensure_ascii=False), key))


def disconnect_bridge(connection: BrowserConnection) -> None:
    for request in list(connection.pending.values()):
        future = request["future"]
        if not future.done():
            future.set_result(failure("browser_disconnected", unknown=request["operation"] == "web.activate"))
    connection.pending.clear()
    key = _dk(connection.owner, connection.device_id)
    if bridges.get(key) is connection:
        bridges.pop(key, None)


def receive_result(connection: BrowserConnection, message: dict) -> None:
    if set(message) - {"type", "request_id", "result"} or not isinstance(message.get("request_id"), str):
        raise ValueError("malformed_browser_message")
    request = connection.pending.get(message["request_id"])
    if not request:
        return  # Late/unknown receipts never complete a different request.
    try:
        result = _safe_result(message.get("result"), request["operation"], request["params"])
    except (ValueError, TypeError):
        result = failure("malformed_browser_result", unknown=request["operation"] == "web.activate")
    future = request["future"]
    if not future.done():
        future.set_result(result)


async def execute_browser_action(user_id: int, task_id: str, device_id: str, operation: str, params: dict, *, external_action: bool = False, cancelled=None) -> dict:
    try:
        require_owned_device(user_id, device_id)
        clean = validate_params(operation, params)
        if not isinstance(task_id, str) or not task_id or len(task_id)>256:
            raise ValueError("invalid_browser_task_id")
        connection = bridges.get(_dk(user_id, device_id))
        if not connection or connection.owner != user_id or connection.device_id != device_id:
            raise ValueError("browser_offline")
        authenticate_credential_hash(connection)
    except ValueError as exc:
        return failure(str(exc))
    if cancelled and cancelled():
        return failure("task_cancelled")
    effect_key = None
    request_id = secrets.token_hex(16)
    if operation == "web.activate":
        effect_key, request_id, cached = _reserve_effect(user_id, task_id, device_id, operation, clean)
        if cached is not None:
            return cached
    future = asyncio.get_running_loop().create_future()
    connection.pending[request_id] = {"future": future, "operation": operation, "params": clean}
    sent = False
    try:
        command = {"type": "command", "request_id": request_id, "operation": operation, "params": clean,
                   "authorization": {"external_action": external_action is True}}
        await asyncio.wait_for(connection.send_lock.acquire(), timeout=3)
        try:
            if cancelled and cancelled():
                result = failure("task_cancelled")
                return result
            if bridges.get(_dk(user_id, device_id)) is not connection:
                result = failure("browser_disconnected")
                return result
            # Set before await: a send exception may occur after delivery.
            sent = True
            await asyncio.wait_for(connection.ws.send_text(json.dumps(command, ensure_ascii=False)), timeout=3)
        finally:
            connection.send_lock.release()
        timeout = (clean.get("timeout_ms", 10000)/1000 + 3) if operation == "web.wait" else RESPONSE_TIMEOUT
        deadline = time.monotonic() + min(timeout, 20)
        while not future.done():
            if cancelled and cancelled():
                result = failure("task_cancelled", unknown=operation == "web.activate")
                break
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                result = failure("browser_timeout", unknown=operation == "web.activate")
                break
            await asyncio.wait({future}, timeout=min(.1, remaining))
        else:
            result = future.result()
    except asyncio.CancelledError:
        result = failure("task_cancelled", unknown=sent and operation == "web.activate")
        raise
    except asyncio.TimeoutError:
        result = failure("browser_timeout", unknown=sent and operation == "web.activate")
    except Exception:
        result = failure("browser_disconnected", unknown=sent and operation == "web.activate")
    finally:
        connection.pending.pop(request_id, None)
        if "result" in locals():
            result["response_policy"] = "silent_on_success" if operation in {"web.fill", "web.activate", "web.wait", "web.focus"} else "speak_result"
        if effect_key:
            _finish_effect(effect_key, locals().get("result", failure("browser_action_unknown", unknown=sent)))
        logger.info("browser action owner=%s device=%s operation=%s request=%s status=%s", user_id, device_id, operation, request_id, locals().get("result", {}).get("status", "unknown"))
    result["response_policy"] = "silent_on_success" if operation in {"web.fill", "web.activate", "web.wait", "web.focus"} else "speak_result"
    return result


def authenticate_credential_hash(connection: BrowserConnection) -> None:
    with db.get_db() as conn:
        row = conn.execute("SELECT owner_user_id,device_id,expires_at FROM browser_credentials WHERE credential_hash=?", (connection.credential_hash,)).fetchone()
    if not row or row["expires_at"] <= time.time() or row["owner_user_id"] != connection.owner or row["device_id"] != connection.device_id:
        raise ValueError("invalid_browser_credential")

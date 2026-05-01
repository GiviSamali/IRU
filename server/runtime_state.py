import time
import uuid
import hashlib
from collections import defaultdict


# Connected agent runtime
devices: dict = {}


# Download tokens
download_tokens: dict = {}
TOKEN_TTL = 1800  # 30 minutes


# In-memory task queue
tasks: dict = {}
TASK_TTL = 3600  # 1 hour


# Declined plan suggestions keyed by chat_id + request hash
declined_plan_requests: dict[str, float] = {}


# Rate limit buckets
rate_counters: dict[str, list[float]] = defaultdict(list)
ip_rate_counters: dict[str, list[float]] = defaultdict(list)


def _dk(user_id: int, device_id: str) -> str:
    """Composite key for device isolation per user."""
    return f"{user_id}:{device_id}"


def _short_did(composite_key: str) -> str:
    """Extract short device_id from composite key."""
    return composite_key.split(":", 1)[1] if ":" in composite_key else composite_key


def get_user_devices(user_id: int) -> dict:
    """Return only devices that belong to the given user."""
    return {did: dev for did, dev in devices.items() if dev.get("user_id") == user_id}


def create_download_token(device_id: str, file_path: str, user_id: int = 0) -> str:
    """Create a temporary token for file downloads."""
    token = str(uuid.uuid4())
    download_tokens[token] = {
        "device_id": _short_did(device_id),
        "file_path": file_path,
        "user_id": user_id,
        "created": time.time(),
    }
    cleanup_expired_download_tokens()
    return token


def cleanup_expired_download_tokens() -> None:
    """Drop expired download tokens."""
    now = time.time()
    expired = [t for t, v in download_tokens.items() if now - v["created"] > TOKEN_TTL]
    for token in expired:
        download_tokens.pop(token, None)


def create_download_link(device_id: str, file_path: str, user_id: int = 0) -> str:
    token = create_download_token(device_id, file_path, user_id=user_id)
    return f"/api/download/{token}"


def _request_hash(message: str) -> str:
    normalized = (message or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def mark_plan_declined(chat_id: int, message: str) -> None:
    if not chat_id or not message:
        return
    declined_plan_requests[f"{chat_id}:{_request_hash(message)}"] = time.time()


def is_plan_declined(chat_id: int, message: str) -> bool:
    if not chat_id or not message:
        return False
    cleanup_old_tasks()
    return f"{chat_id}:{_request_hash(message)}" in declined_plan_requests


def cleanup_old_tasks() -> None:
    """Remove tasks older than TASK_TTL."""
    now = time.time()
    expired = [tid for tid, task in tasks.items() if now - task["created_at"] > TASK_TTL]
    for task_id in expired:
        tasks.pop(task_id, None)
    expired_declines = [key for key, created_at in declined_plan_requests.items() if now - created_at > TASK_TTL]
    for key in expired_declines:
        declined_plan_requests.pop(key, None)

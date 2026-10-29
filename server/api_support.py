import re
import time
import hashlib
import json

from fastapi import HTTPException, Request

try:
    from .auth import is_jwt, verify_access_token
    from .database import get_user_by_id, get_user_by_token
    from .runtime_state import ip_rate_counters, rate_counters
except ImportError:
    from auth import is_jwt, verify_access_token
    from database import get_user_by_id, get_user_by_token
    from runtime_state import ip_rate_counters, rate_counters


ADMIN_USER_ID = 1


def _is_admin(user: dict) -> bool:
    """Admin access check by id, not by username."""
    return user.get("id") == ADMIN_USER_ID


RATE_LIMIT = 30
RATE_WINDOW = 60
IP_RATE_LIMIT = 10
IP_RATE_WINDOW = 60


def check_rate_limit(user_id: str) -> bool:
    """Return True when the per-user rate limit is still allowed."""
    now = time.time()
    window_start = now - RATE_WINDOW
    rate_counters[user_id] = [ts for ts in rate_counters[user_id] if ts > window_start]
    if len(rate_counters[user_id]) >= RATE_LIMIT:
        return False
    rate_counters[user_id].append(now)
    return True


def check_ip_rate_limit(ip: str) -> bool:
    """Return True when the per-IP rate limit is still allowed."""
    now = time.time()
    window_start = now - IP_RATE_WINDOW
    ip_rate_counters[ip] = [ts for ts in ip_rate_counters[ip] if ts > window_start]
    if len(ip_rate_counters[ip]) >= IP_RATE_LIMIT:
        return False
    ip_rate_counters[ip].append(now)
    return True


DANGEROUS_PATTERNS = [
    r"format\s+[a-z]:",
    r"diskpart",
    r"bcdedit",
    r"rm\s+-rf\s+/",
    r"rmdir\s+/s\s+/q\s+[a-z]:\\\\",
    r"del\s+/[sfq].*\\windows",
    r"reg\s+delete\s+hklm",
    r"net\s+stop\s+(windefend|mpssvc|wuauserv)",
    r"powershell.*downloadstring",
    r"powershell.*downloadfile.*\|.*iex",
    r"certutil.*-urlcache.*-split",
    r"bitsadmin.*transfer",
    r"net\s+user\s+.*\s+/add",
    r"net\s+localgroup\s+administrators",
    r"netsh\s+advfirewall\s+set.*state\s+off",
    r"cipher\s+/e",
]


DELETE_PATTERNS = [
    r"\bremove-item\b",
    r"\bdel\s+",
    r"\brd\s+",
    r"\brmdir\s+",
    r"\brm\s+",
    r"\bclear-content\b",
    r"\b(?:os\.(?:remove|unlink|rmdir)|shutil\.rmtree)\s*\(",
    r"\.(?:unlink|rmdir)\s*\(",
    r"\bIO\.(?:File|Directory)\]::Delete\s*\(",
]


CONFIRM_PATTERNS = DELETE_PATTERNS + [
    r"stop-process",
    r"\bkill\s+",
    r"taskkill",
    r"shutdown",
    r"restart-computer",
    r"uninstall",
]


_dangerous_re = [re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_PATTERNS]
_confirm_re = [re.compile(pattern, re.IGNORECASE) for pattern in CONFIRM_PATTERNS]
_delete_re = [re.compile(pattern, re.IGNORECASE) for pattern in DELETE_PATTERNS]


def is_deletion_command(command: str) -> bool:
    """Recognize explicit deletion in command text, not arbitrary script contents."""
    return any(pattern.search(command) for pattern in _delete_re)


def is_command_safe(command: str) -> bool:
    """Return False if the command matches a forbidden pattern."""
    return not any(pattern.search(command) for pattern in _dangerous_re)


def needs_confirmation(command: str) -> bool:
    """Return True if the command should be user-confirmed."""
    return any(pattern.search(command) for pattern in _confirm_re)


def action_fingerprint(action, params):
    return hashlib.sha256(json.dumps([action, params], sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def assess_action(action, params, inspection=None):
    """Classify the existing command patterns; opaque code remains allowed.

    inspection is reserved for trusted observations, never Worker-provided risk.
    Script analysis and specialised-tool effect expansion are deliberately excluded.
    """
    command = str(params.get("command") or "") if action == "execute_cmd" else ""
    denied = any(pattern.search(command) for pattern in _dangerous_re)
    deletion = any(pattern.search(command) for pattern in _delete_re)
    confirm = deletion or any(pattern.search(command) for pattern in _confirm_re)
    decision = "DENY" if denied else "CONFIRM" if confirm else "ALLOW"
    kind = "deletion" if deletion else "dangerous" if denied or confirm else "command"
    return {"decision": decision, "reason_code": "command_forbidden" if denied else
            "command_confirmation_required" if confirm else "allowed",
            "findings": [{"kind": kind}] if denied or confirm else [],
            "fingerprint": action_fingerprint(action, params),
            "confirmation_channel": "button" if denied or confirm else "voice_or_button"}


class ActionConfirmationRequired(RuntimeError):
    def __init__(self, assessment):
        super().__init__("CONFIRM_REQUIRED: Команда требует подтверждения пользователя.")
        self.assessment = assessment


def is_recovery_observation(action, params):
    """Known observations only; shell/launch and Worker risk labels prove nothing."""
    try:
        from .tool_registry import canonical_tool_name
    except ImportError:
        from tool_registry import canonical_tool_name
    action = canonical_tool_name(action)
    if action in {"office_docx", "office_xlsx", "office_pptx"}:
        return params.get("action") == "inspect"
    if action == "window.control":
        return params.get("action") in {"active", "list", "find", "monitors"}
    if action == "device.refresh_state":
        return params.get("mode", "snapshot") == "snapshot"
    return action in {"list_dir", "read_file", "get_file_content", "device.get_cached_passport", "device.get_passport",
                      "window.list", "window.find", "window.verify", "app.verify_launch",
                      "web.tabs", "web.read", "web.elements", "web.wait", "memory.get_stats",
                      "memory.list_facts", "system.list_tools", "system.get_last_run_summary"}


def get_current_user(request: Request) -> dict:
    """
    Extract the user from:
      1. Authorization: Bearer <jwt>
      2. X-Token: <jwt_or_uuid>
      3. ?token=<uuid>
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.headers.get("X-Token") or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется токен авторизации")

    if is_jwt(token):
        payload = verify_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Токен истёк или недействителен")
        user = get_user_by_id(int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        return user

    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return user


def _client_ip(request: Request) -> str:
    """Get client IP (supports X-Forwarded-For behind reverse proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

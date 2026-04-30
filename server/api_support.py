import re
import time

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


CONFIRM_PATTERNS = [
    r"remove-item",
    r"del\s+",
    r"rd\s+",
    r"rmdir\s+",
    r"rm\s+",
    r"stop-process",
    r"kill\s+",
    r"taskkill",
    r"shutdown",
    r"restart-computer",
    r"clear-content",
    r"uninstall",
]


_dangerous_re = [re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_PATTERNS]
_confirm_re = [re.compile(pattern, re.IGNORECASE) for pattern in CONFIRM_PATTERNS]


def is_command_safe(command: str) -> bool:
    """Return False if the command matches a forbidden pattern."""
    return not any(pattern.search(command) for pattern in _dangerous_re)


def needs_confirmation(command: str) -> bool:
    """Return True if the command should be user-confirmed."""
    return any(pattern.search(command) for pattern in _confirm_re)


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

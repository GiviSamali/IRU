"""
auth.py — JWT авторизация ИРУ

Схема:
  - Access token:  JWT, живёт 8 часов, содержит user_id + name + plan
  - Refresh token: UUID, живёт 30 дней, хранится в БД (таблица refresh_tokens)

Эндпоинты (добавляются в main.py):
  POST /api/login   — принимает старый статичный токен → возвращает access + refresh JWT
  POST /api/refresh — принимает refresh token → возвращает новый access token
  POST /api/logout  — инвалидирует refresh token

Совместимость:
  get_current_user() поддерживает оба формата:
    - JWT (новый формат, eyJ...)
    - Статичный UUID токен (старый формат, для обратной совместимости)
"""

import uuid
import time
import hmac
import hashlib
import base64
import json
from pathlib import Path

# ── Секретный ключ ───────────────────────────────────────────────────────
# Хранится в файле рядом с БД. При первом запуске генерируется автоматически.
_SECRET_FILE = Path(__file__).parent / ".jwt_secret"

def _get_secret() -> bytes:
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    secret = uuid.uuid4().hex + uuid.uuid4().hex  # 64 символа
    _SECRET_FILE.write_text(secret)
    return secret.encode()

SECRET = _get_secret()

# ── Время жизни токенов ──────────────────────────────────────────────────
ACCESS_TTL  = 8 * 3600   # 8 часов
REFRESH_TTL = 30 * 86400 # 30 дней


# ── JWT (без внешних библиотек, чистый Python) ───────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))

def _sign(data: str) -> str:
    sig = hmac.new(SECRET, data.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)

def create_access_token(user_id: int, name: str, plan: str) -> str:
    """Создать JWT access token."""
    header  = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub":  str(user_id),
        "name": name,
        "plan": plan,
        "exp":  int(time.time()) + ACCESS_TTL,
        "iat":  int(time.time()),
    }).encode())
    signature = _sign(f"{header}.{payload}")
    return f"{header}.{payload}.{signature}"

def verify_access_token(token: str) -> dict | None:
    """
    Проверить JWT access token.
    Возвращает payload dict или None если невалидный/истёкший.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        # Проверка подписи
        expected_sig = _sign(f"{header}.{payload}")
        if not hmac.compare_digest(signature, expected_sig):
            return None
        # Декодировать payload
        data = json.loads(_b64url_decode(payload))
        # Проверка срока жизни
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


# ── Refresh tokens ───────────────────────────────────────────────────────

def create_refresh_token() -> str:
    """Создать refresh token (UUID)."""
    return str(uuid.uuid4())

def is_jwt(token: str) -> bool:
    """Определить, является ли токен JWT (начинается с eyJ)."""
    return token.startswith("eyJ")

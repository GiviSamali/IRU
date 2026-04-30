from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from ..api_support import _client_ip, check_ip_rate_limit, get_current_user
    from ..auth import ACCESS_TTL, REFRESH_TTL, create_access_token, create_refresh_token
    from ..database import (
        PLAN_LIMITS,
        accept_terms,
        add_audit_log,
        check_daily_command_limit,
        cleanup_expired_refresh_tokens,
        get_user_by_id,
        get_user_by_token,
        get_user_plan,
        has_accepted_terms,
        revoke_refresh_token,
        set_user_consent,
        store_refresh_token,
        get_refresh_token,
    )
    from ..runtime_state import get_user_devices
except ImportError:
    from api_support import _client_ip, check_ip_rate_limit, get_current_user
    from auth import ACCESS_TTL, REFRESH_TTL, create_access_token, create_refresh_token
    from database import (
        PLAN_LIMITS,
        accept_terms,
        add_audit_log,
        check_daily_command_limit,
        cleanup_expired_refresh_tokens,
        get_user_by_id,
        get_user_by_token,
        get_user_plan,
        has_accepted_terms,
        revoke_refresh_token,
        set_user_consent,
        store_refresh_token,
        get_refresh_token,
    )
    from runtime_state import get_user_devices


router = APIRouter()


class AuthRequest(BaseModel):
    token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ConsentRequest(BaseModel):
    consent: bool


@router.post("/api/auth")
async def auth(body: AuthRequest, request: Request):
    client_ip = _client_ip(request)
    if not check_ip_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"status": "error", "error": "Слишком много попыток входа. Подождите минуту."})
    user = get_user_by_token(body.token)
    if not user:
        add_audit_log(None, None, "login_failed", f"token={body.token[:8]}...", _client_ip(request))
        return JSONResponse(status_code=401, content={"status": "error", "error": "Недействительный токен"})
    plan = user.get("plan") or "free"
    access = create_access_token(user["id"], user["name"], plan)
    refresh = create_refresh_token()
    store_refresh_token(user["id"], refresh, REFRESH_TTL)
    add_audit_log(user["id"], user["name"], "login", None, _client_ip(request))
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "token": user["token"],
            "data_consent": bool(user.get("data_consent", 0)),
        },
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": ACCESS_TTL,
    }


@router.post("/api/refresh")
async def refresh_token_endpoint(body: RefreshRequest, request: Request):
    rt = get_refresh_token(body.refresh_token)
    if not rt:
        return JSONResponse(status_code=401, content={"status": "error", "error": "Refresh token недействителен или истёк"})
    user = get_user_by_id(rt["user_id"])
    if not user:
        revoke_refresh_token(body.refresh_token)
        return JSONResponse(status_code=401, content={"status": "error", "error": "Пользователь не найден"})
    plan = user.get("plan") or "free"
    access = create_access_token(user["id"], user["name"], plan)
    add_audit_log(user["id"], user["name"], "token_refresh", None, _client_ip(request))
    return {"status": "ok", "access_token": access, "expires_in": ACCESS_TTL}


@router.post("/api/logout")
async def logout(body: LogoutRequest, request: Request):
    revoke_refresh_token(body.refresh_token)
    try:
        user = get_current_user(request)
        add_audit_log(user["id"], user["name"], "logout", None, _client_ip(request))
    except Exception:
        add_audit_log(None, None, "logout", None, _client_ip(request))
    return {"status": "ok"}


@router.post("/api/consent")
async def api_set_consent(body: ConsentRequest, request: Request):
    user = get_current_user(request)
    ok = set_user_consent(user["id"], body.consent)
    return {"status": "ok" if ok else "error"}


@router.get("/api/user_info")
async def api_user_info(request: Request):
    user = get_current_user(request)
    plan = get_user_plan(user["id"])
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    cmd_usage = check_daily_command_limit(user["id"])
    dev_count = len(get_user_devices(user["id"]))
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "plan": plan,
            "limits": limits,
            "commands_today": cmd_usage["used"],
            "commands_limit": cmd_usage["limit"],
            "devices_count": dev_count,
            "devices_limit": limits["max_devices"],
            "terms_accepted": has_accepted_terms(user["id"]),
        },
    }


@router.post("/api/accept_terms")
async def api_accept_terms(request: Request):
    user = get_current_user(request)
    accept_terms(user["id"])
    return {"status": "ok"}


@router.get("/api/terms_status")
async def api_terms_status(request: Request):
    user = get_current_user(request)
    return {"status": "ok", "accepted": has_accepted_terms(user["id"])}

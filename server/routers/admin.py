from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

try:
    from ..api_support import _client_ip, _is_admin, get_current_user
    from ..database import (
        PLAN_LIMITS,
        add_audit_log,
        create_user,
        delete_user,
        get_audit_log,
        get_audit_log_count,
        get_training_count,
        get_training_data,
        list_users,
        set_user_plan,
    )
except ImportError:
    from api_support import _client_ip, _is_admin, get_current_user
    from database import (
        PLAN_LIMITS,
        add_audit_log,
        create_user,
        delete_user,
        get_audit_log,
        get_audit_log_count,
        get_training_count,
        get_training_data,
        list_users,
        set_user_plan,
    )


router = APIRouter()


class CreateUserRequest(BaseModel):
    name: str


class SetPlanRequest(BaseModel):
    plan: str


@router.get("/api/admin/users")
async def admin_list_users(request: Request):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    return {"status": "ok", "users": list_users()}


@router.post("/api/admin/users")
async def admin_create_user(body: CreateUserRequest, request: Request):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    new_user = create_user(body.name)
    add_audit_log(user["id"], user["name"], "admin_create_user", f"new_user={body.name} id={new_user['id']}", _client_ip(request))
    return {"status": "ok", "user": new_user}


@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    ok = delete_user(user_id)
    if ok:
        add_audit_log(user["id"], user["name"], "admin_delete_user", f"deleted_user_id={user_id}", _client_ip(request))
    return {"status": "ok" if ok else "error", "deleted": ok}


@router.get("/api/admin/training")
async def admin_training_data(request: Request, limit: int = 100, offset: int = 0):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    return {"status": "ok", "data": get_training_data(limit, offset), "total": get_training_count()}


@router.patch("/api/admin/users/{user_id}/plan")
async def api_admin_set_plan(user_id: int, body: SetPlanRequest, request: Request):
    admin = get_current_user(request)
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администратора")
    if body.plan not in PLAN_LIMITS:
        return {"status": "error", "error": f"Неизвестный план: {body.plan}. Доступны: free, pro, business"}
    ok = set_user_plan(user_id, body.plan)
    if not ok:
        return {"status": "error", "error": "Пользователь не найден"}
    add_audit_log(admin["id"], admin["name"], "admin_set_plan", f"user_id={user_id} plan={body.plan}", _client_ip(request))
    return {"status": "ok", "user_id": user_id, "plan": body.plan}


@router.get("/api/admin/audit")
async def api_admin_audit(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None),
):
    admin = get_current_user(request)
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администратора")
    logs = get_audit_log(limit=limit, offset=offset, user_id=user_id)
    total = get_audit_log_count(user_id=user_id)
    return {"status": "ok", "logs": logs, "total": total}

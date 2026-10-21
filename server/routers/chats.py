from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from ..api_support import get_current_user
    from ..database import create_chat, delete_chat, get_chat, get_messages, list_chats, update_chat_title
except ImportError:
    from api_support import get_current_user
    from database import create_chat, delete_chat, get_chat, get_messages, list_chats, update_chat_title


router = APIRouter()


class CreateChatRequest(BaseModel):
    title: str = ""


class RenameChatRequest(BaseModel):
    title: str


@router.post("/api/chats")
async def api_create_chat(body: CreateChatRequest, request: Request):
    user = get_current_user(request)
    title = body.title.strip() or "Новый чат"
    chat = create_chat(user["id"], title)
    return {"status": "ok", "chat": chat}


@router.get("/api/chats")
async def api_list_chats(request: Request):
    user = get_current_user(request)
    return {"status": "ok", "chats": list_chats(user["id"])}


@router.get("/api/chats/{chat_id}/messages")
async def api_get_messages(chat_id: int, request: Request):
    user = get_current_user(request)
    chat = get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    messages = get_messages(chat_id, limit=100, include_worker_commands=False,
                            include_result_first_workers=False)
    # The human sees one Orchestrator turn per Goal. Project its current state
    # from owned, durable Worker results; never use worker prose as the verdict.
    try:
        from ..worker_scheduler import goal_view
        from ..goal_presentation import goal_presentation
    except ImportError:
        from worker_scheduler import goal_view
        from goal_presentation import goal_presentation
    cache = {}
    for message in messages:
        if (message.get("taskKind") != "orchestrator"
                or not message.get("resultFirst") or not message.get("goalId")):
            continue
        gid = message["goalId"]
        if gid not in cache:
            try:
                cache[gid] = goal_presentation(goal_view(gid, user["id"], chat_id))
            except ValueError:
                cache[gid] = None
        presentation = cache[gid]
        if presentation is None:
            message.update(content="Результат цели недоступен.",taskStatus="unknown",taskKind="goal")
        else:
            message.update(content=presentation["message"],taskStatus=presentation["status"],
                           taskKind="goal",goalPresentation=presentation)
    return {"status": "ok", "messages": messages}


@router.patch("/api/chats/{chat_id}")
async def api_rename_chat(chat_id: int, body: RenameChatRequest, request: Request):
    user = get_current_user(request)
    ok = update_chat_title(chat_id, user["id"], body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"status": "ok"}


@router.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: int, request: Request):
    user = get_current_user(request)
    if not get_chat(chat_id, user["id"]):
        return {"status": "error", "deleted": False}
    try:
        from ..worker_scheduler import scheduler
    except ImportError:
        from worker_scheduler import scheduler
    # A chat delete revokes authority for its unfinished work. The group cancel
    # requests cancellation of active tools and stops queued/dependent work.
    # Both operations are synchronous until they return, so this server process
    # cannot schedule a sibling between cancellation and deletion.
    await scheduler.cancel_group(user["id"], chat_id, scope="chat")
    ok = delete_chat(chat_id, user["id"])
    return {"status": "ok" if ok else "error", "deleted": ok}

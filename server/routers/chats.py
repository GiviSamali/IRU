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
    return {"status": "ok", "messages": get_messages(chat_id, limit=50)}


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
    ok = delete_chat(chat_id, user["id"])
    return {"status": "ok" if ok else "error", "deleted": ok}

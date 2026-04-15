"""
main.py — FastAPI-сервер ИРУ v3.2

Эндпоинты:
  GET  /                         — отдаёт index.html
  POST /api/auth                 — авторизация по токену
  GET  /api/devices              — устройства текущего пользователя
  POST /api/chats                — создать чат
  GET  /api/chats                — список чатов пользователя
  GET  /api/chats/{id}/messages  — сообщения чата
  DELETE /api/chats/{id}         — удалить чат
  PATCH /api/chats/{id}          — переименовать чат
  POST /command                  — прямая команда агенту
  POST /nl_command               — NL команда → LLM → агент (в контексте чата)
  GET  /api/download/{token}     — скачать файл по временному токену
  POST /api/download_request     — запрос на скачивание (проводник)
  WS   /ws/{device_id}           — WebSocket для агентов

  Админ-эндпоинты:
  GET  /api/admin/users          — список пользователей
  POST /api/admin/users          — создать пользователя
  DELETE /api/admin/users/{id}   — удалить пользователя

Авторизация: заголовок X-Token или query-параметр ?token=
Агенты: query-параметр ?user_token= при подключении WS
"""

import json
import asyncio
import uuid
import base64
import time
from pathlib import Path
from contextlib import asynccontextmanager
from io import BytesIO

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from controller import process_nl_command
from database import (
    init_db, get_user_by_token, create_user, list_users, delete_user,
    create_chat, list_chats, get_chat, update_chat_title, delete_chat,
    add_message, get_messages,
)

# ── Хранение подключённых устройств ───────────────────────────────────────
# {
#   "device_id": {
#       "ws": WebSocket,
#       "info": {"os": ..., "hostname": ..., ...},
#       "pending": {cmd_id: asyncio.Future, ...},
#       "user_id": int  # владелец устройства
#   }
# }
devices: dict = {}

# ── Токены для скачивания файлов ─────────────────────────────────────────
download_tokens: dict = {}
TOKEN_TTL = 300  # 5 минут

# ── Конфиг админ-токена ──────────────────────────────────────────────────
ADMIN_CONFIG_PATH = Path(__file__).parent / "admin_config.json"


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[server] ИРУ v3.2 запущен")
    yield
    print("[server] ИРУ v3.2 остановлен")


app = FastAPI(title="ИРУ v3.2", lifespan=lifespan)

# Статические файлы (UI)
STATIC_DIR = Path(__file__).parent.parent / "ui"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Модели запросов ──────────────────────────────────────────────────────

class DirectCommand(BaseModel):
    device_id: str
    action: str
    params: dict = {}

class NLCommand(BaseModel):
    device_id: str
    message: str
    chat_id: int | None = None  # если указан — работает в контексте чата

class AuthRequest(BaseModel):
    token: str

class CreateChatRequest(BaseModel):
    title: str = ""

class RenameChatRequest(BaseModel):
    title: str

class CreateUserRequest(BaseModel):
    name: str


# ── Авторизация ──────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict:
    """Извлечь пользователя из заголовка X-Token или query ?token=."""
    token = request.headers.get("X-Token") or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется токен авторизации")
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return user


def get_user_devices(user_id: int) -> dict:
    """Получить устройства, принадлежащие пользователю."""
    return {did: dev for did, dev in devices.items() if dev.get("user_id") == user_id}


# ── Утилиты ──────────────────────────────────────────────────────────────

async def send_command_to_agent(device_id: str, action: str, params: dict) -> dict:
    """Отправить команду конкретному агенту и дождаться ответа."""
    dev = devices.get(device_id)
    if not dev:
        raise RuntimeError(f"Устройство '{device_id}' не подключено")

    cmd_id = str(uuid.uuid4())[:8]
    future = asyncio.get_event_loop().create_future()
    dev["pending"][cmd_id] = future

    msg = json.dumps({
        "type": "command",
        "payload": {"id": cmd_id, "action": action, "params": params}
    })
    await dev["ws"].send_text(msg)

    try:
        result = await asyncio.wait_for(future, timeout=60.0)
    except asyncio.TimeoutError:
        dev["pending"].pop(cmd_id, None)
        raise RuntimeError("Таймаут ожидания ответа от агента")

    return result


def create_download_token(device_id: str, file_path: str) -> str:
    """Создать временный токен для скачивания файла."""
    token = str(uuid.uuid4())
    download_tokens[token] = {
        "device_id": device_id,
        "file_path": file_path,
        "created": time.time(),
    }
    now = time.time()
    expired = [t for t, v in download_tokens.items() if now - v["created"] > TOKEN_TTL]
    for t in expired:
        download_tokens.pop(t, None)
    return token


def get_file_link_fn(device_id: str, file_path: str) -> str:
    """Создать ссылку для скачивания файла (для LLM)."""
    token = create_download_token(device_id, file_path)
    return f"/api/download/{token}"


# ── HTML ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "ui" / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ИРУ v3.2 — UI не найден</h1>")


# ── AUTH API ─────────────────────────────────────────────────────────────

@app.post("/api/auth")
async def auth(body: AuthRequest):
    """Авторизация по токену. Возвращает информацию о пользователе."""
    user = get_user_by_token(body.token)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "error": "Недействительный токен"}
        )
    return {
        "status": "ok",
        "user": {"id": user["id"], "name": user["name"], "token": user["token"]}
    }


# ── ADMIN API ────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    """Список всех пользователей (только для admin)."""
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    users = list_users()
    return {"status": "ok", "users": users}


@app.post("/api/admin/users")
async def admin_create_user(body: CreateUserRequest, request: Request):
    """Создать нового пользователя (только для admin)."""
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    new_user = create_user(body.name)
    return {"status": "ok", "user": new_user}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    """Удалить пользователя (только для admin)."""
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    ok = delete_user(user_id)
    return {"status": "ok" if ok else "error", "deleted": ok}


# ── DEVICES API ──────────────────────────────────────────────────────────

@app.get("/api/devices")
async def get_devices(request: Request):
    """Список устройств текущего пользователя."""
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    result = {}
    for did, dev in user_devs.items():
        result[did] = {
            "device_id": did,
            "info": dev.get("info", {}),
            "connected": True,
        }
    return {"devices": result}


# ── CHATS API ────────────────────────────────────────────────────────────

@app.post("/api/chats")
async def api_create_chat(body: CreateChatRequest, request: Request):
    """Создать новый чат."""
    user = get_current_user(request)
    title = body.title.strip() or "Новый чат"
    chat = create_chat(user["id"], title)
    return {"status": "ok", "chat": chat}


@app.get("/api/chats")
async def api_list_chats(request: Request):
    """Список чатов пользователя."""
    user = get_current_user(request)
    chats = list_chats(user["id"])
    return {"status": "ok", "chats": chats}


@app.get("/api/chats/{chat_id}/messages")
async def api_get_messages(chat_id: int, request: Request):
    """Сообщения чата (последние 50)."""
    user = get_current_user(request)
    chat = get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    msgs = get_messages(chat_id, limit=50)
    return {"status": "ok", "messages": msgs}


@app.patch("/api/chats/{chat_id}")
async def api_rename_chat(chat_id: int, body: RenameChatRequest, request: Request):
    """Переименовать чат."""
    user = get_current_user(request)
    ok = update_chat_title(chat_id, user["id"], body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"status": "ok"}


@app.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: int, request: Request):
    """Удалить чат."""
    user = get_current_user(request)
    ok = delete_chat(chat_id, user["id"])
    return {"status": "ok" if ok else "error", "deleted": ok}


# ── COMMAND API ──────────────────────────────────────────────────────────

@app.post("/command")
async def direct_command(cmd: DirectCommand, request: Request):
    """Прямая команда агенту (без LLM). Проверка доступа к устройству."""
    user = get_current_user(request)
    dev = devices.get(cmd.device_id)
    if not dev or dev.get("user_id") != user["id"]:
        return {"status": "error", "error": "Устройство не найдено или нет доступа"}
    try:
        result = await send_command_to_agent(cmd.device_id, cmd.action, cmd.params)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/nl_command")
async def nl_command(cmd: NLCommand, request: Request):
    """Команда на естественном языке → LLM → агент. С памятью чата."""
    user = get_current_user(request)

    dev = devices.get(cmd.device_id)
    if not dev or dev.get("user_id") != user["id"]:
        return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено или нет доступа"}

    device_info = dev.get("info", {})

    # Устройства только этого пользователя
    user_devs = get_user_devices(user["id"])
    all_devices_info = {}
    for did, d in user_devs.items():
        all_devices_info[did] = {"info": d.get("info", {})}

    # Автоматически создать чат, если не указан
    chat_id = cmd.chat_id
    if not chat_id:
        # Новый чат с названием из первого сообщения
        title = cmd.message[:50].strip()
        if not title:
            title = "Новый чат"
        chat = create_chat(user["id"], title)
        chat_id = chat["id"]
    else:
        # Проверить, что чат принадлежит пользователю
        chat = get_chat(chat_id, user["id"])
        if not chat:
            return {"status": "error", "error": "Чат не найден"}

    # Сохранить сообщение пользователя
    add_message(chat_id, "user", cmd.message)

    # Загрузить историю чата для контекста LLM (последние 50 сообщений)
    chat_history = get_messages(chat_id, limit=50)

    async def send_fn(target_device_id, action, params):
        # Проверить, что целевое устройство принадлежит пользователю
        target_dev = devices.get(target_device_id)
        if not target_dev or target_dev.get("user_id") != user["id"]:
            raise RuntimeError(f"Нет доступа к устройству '{target_device_id}'")
        return await send_command_to_agent(target_device_id, action, params)

    try:
        result = await process_nl_command(
            user_message=cmd.message,
            device_id=cmd.device_id,
            device_info=device_info,
            all_devices=all_devices_info,
            send_command_fn=send_fn,
            get_file_link_fn=get_file_link_fn,
            chat_history=chat_history,
        )

        # Сохранить ответ ассистента
        add_message(chat_id, "assistant", result.get("answer", ""), result.get("commands"))

        return {"status": "ok", "chat_id": chat_id, **result}
    except httpx.HTTPStatusError as e:
        error_msg = f"Ошибка LLM API: {e.response.status_code} — {e.response.text[:200]}"
        add_message(chat_id, "assistant", error_msg)
        return {"status": "error", "chat_id": chat_id, "error": error_msg}
    except Exception as e:
        error_msg = str(e)
        add_message(chat_id, "assistant", f"Ошибка: {error_msg}")
        return {"status": "error", "chat_id": chat_id, "error": error_msg}


# ── DOWNLOAD API ─────────────────────────────────────────────────────────

@app.get("/api/download/{token}")
async def download_file(token: str):
    """Скачать файл по временному токену."""
    info = download_tokens.pop(token, None)
    if not info:
        return {"status": "error", "error": "Ссылка недействительна или истекла"}

    if time.time() - info["created"] > TOKEN_TTL:
        return {"status": "error", "error": "Ссылка истекла"}

    device_id = info["device_id"]
    file_path = info["file_path"]

    try:
        result = await send_command_to_agent(
            device_id, "get_file_content", {"path": file_path}
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if "error" in result and result["error"]:
        return {"status": "error", "error": result["error"]}

    data = base64.b64decode(result["data_b64"])
    filename = result.get("filename", "file")

    return StreamingResponse(
        BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/download_request")
async def download_request(body: dict, request: Request):
    """Запрос на скачивание файла из UI (проводник)."""
    user = get_current_user(request)
    device_id = body.get("device_id")
    file_path = body.get("file_path")
    if not device_id or not file_path:
        return {"status": "error", "error": "device_id и file_path обязательны"}

    dev = devices.get(device_id)
    if not dev or dev.get("user_id") != user["id"]:
        return {"status": "error", "error": "Нет доступа к устройству"}

    token = create_download_token(device_id, file_path)
    return {"status": "ok", "url": f"/api/download/{token}"}


# ── WebSocket для агентов ────────────────────────────────────────────────

@app.websocket("/ws/{device_id}")
async def websocket_agent(ws: WebSocket, device_id: str, user_token: str = Query(default="")):
    """
    WebSocket для агентов.
    Агент подключается с ?user_token=... — привязывается к пользователю.
    """
    # Проверить токен пользователя
    user = get_user_by_token(user_token) if user_token else None
    if not user:
        await ws.close(code=4001, reason="Недействительный токен пользователя")
        return

    await ws.accept()
    print(f"[ws] agent connected: {device_id} (user: {user['name']})")

    devices[device_id] = {
        "ws": ws,
        "info": {},
        "pending": {},
        "user_id": user["id"],
    }

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "register":
                devices[device_id]["info"] = data.get("payload", {})
                print(f"[ws] registered: {device_id} — {data.get('payload', {})}")

            elif msg_type == "result":
                payload = data.get("payload", {})
                cmd_id = payload.get("id")
                future = devices[device_id]["pending"].pop(cmd_id, None)
                if future and not future.done():
                    if payload.get("status") == "ok":
                        future.set_result(payload.get("result", {}))
                    else:
                        future.set_result({"error": payload.get("error", "Неизвестная ошибка")})

    except WebSocketDisconnect:
        print(f"[ws] agent disconnected: {device_id}")
    except Exception as e:
        print(f"[ws] error for {device_id}: {e}")
    finally:
        devices.pop(device_id, None)


# ── Запуск ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

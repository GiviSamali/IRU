"""
main.py — FastAPI-сервер ИРУ v3.3

Новое в v3.3:
  - Параллельное выполнение: задачи выполняются в фоне, UI не блокируется
  - Broadcast: одна команда на все/выбранные устройства одновременно
  - GET /api/tasks — список активных/завершённых задач
  - GET /api/tasks/{task_id} — статус конкретной задачи

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
  POST /nl_command               — NL команда → фоновая задача (возвращает task_id)
  GET  /api/tasks                — список задач пользователя
  GET  /api/tasks/{task_id}      — статус задачи
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
devices: dict = {}

# ── Токены для скачивания файлов ─────────────────────────────────────────
download_tokens: dict = {}
TOKEN_TTL = 300  # 5 минут

# ── Очередь задач (in-memory) ────────────────────────────────────────────
# task_id -> {
#   "task_id": str,
#   "user_id": int,
#   "chat_id": int,
#   "message": str,
#   "device_ids": [str],       # на каких устройствах
#   "status": "running"|"done"|"error",
#   "results": {device_id: {...}},  # результаты по устройствам
#   "created_at": float,
# }
tasks: dict = {}
TASK_TTL = 3600  # хранить задачи 1 час


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[server] ИРУ v3.3 запущен")
    yield
    print("[server] ИРУ v3.3 остановлен")


app = FastAPI(title="ИРУ v3.3", lifespan=lifespan)

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
    chat_id: int | None = None
    broadcast: bool = False  # отправить на все устройства
    device_ids: list[str] = []  # конкретные устройства (если broadcast=False)

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


def cleanup_old_tasks():
    """Удалить задачи старше TASK_TTL."""
    now = time.time()
    expired = [tid for tid, t in tasks.items() if now - t["created_at"] > TASK_TTL]
    for tid in expired:
        tasks.pop(tid, None)


# ── Фоновое выполнение задачи ────────────────────────────────────────────

async def run_nl_task(task_id: str, user_id: int, message: str,
                      device_ids: list[str], chat_id: int):
    """
    Выполнить NL-задачу в фоне на одном или нескольких устройствах параллельно.
    Результаты записываются в tasks[task_id].
    """
    task = tasks[task_id]

    async def run_on_device(device_id: str):
        """Выполнить задачу на одном устройстве."""
        dev = devices.get(device_id)
        if not dev or dev.get("user_id") != user_id:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Устройство '{device_id}' не найдено или нет доступа",
                "commands": [],
            }

        device_info = dev.get("info", {})

        # Устройства пользователя
        user_devs = get_user_devices(user_id)
        all_devices_info = {did: {"info": d.get("info", {})} for did, d in user_devs.items()}

        # Загрузить историю чата
        chat_history = get_messages(chat_id, limit=50)

        async def send_fn(target_device_id, action, params):
            target_dev = devices.get(target_device_id)
            if not target_dev or target_dev.get("user_id") != user_id:
                raise RuntimeError(f"Нет доступа к устройству '{target_device_id}'")
            return await send_command_to_agent(target_device_id, action, params)

        try:
            result = await process_nl_command(
                user_message=message,
                device_id=device_id,
                device_info=device_info,
                all_devices=all_devices_info,
                send_command_fn=send_fn,
                get_file_link_fn=get_file_link_fn,
                chat_history=chat_history,
            )
            return {
                "device_id": device_id,
                "status": "ok",
                "answer": result.get("answer", ""),
                "commands": result.get("commands", []),
            }
        except httpx.HTTPStatusError as e:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка LLM API: {e.response.status_code}",
                "commands": [],
            }
        except Exception as e:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка: {str(e)}",
                "commands": [],
            }

    try:
        # Выполняем на всех устройствах параллельно
        coros = [run_on_device(did) for did in device_ids]
        results_list = await asyncio.gather(*coros, return_exceptions=True)

        all_commands = []
        answers = []

        for r in results_list:
            if isinstance(r, Exception):
                answers.append(f"Ошибка: {str(r)}")
            else:
                task["results"][r["device_id"]] = r
                if r.get("commands"):
                    all_commands.extend(r["commands"])
                if len(device_ids) > 1:
                    # Мультиустройство: добавляем имя устройства к ответу
                    dev = devices.get(r["device_id"])
                    hostname = dev["info"].get("hostname", r["device_id"]) if dev else r["device_id"]
                    answers.append(f"[{hostname}] {r.get('answer', '')}")
                else:
                    answers.append(r.get("answer", ""))

        combined_answer = "\n\n".join(answers) if answers else "Готово."
        combined_commands = all_commands

        # Сохранить ответ в чат
        add_message(chat_id, "assistant", combined_answer, combined_commands)

        task["status"] = "done"
        task["answer"] = combined_answer
        task["commands"] = combined_commands

    except Exception as e:
        task["status"] = "error"
        task["answer"] = f"Ошибка: {str(e)}"
        task["commands"] = []
        add_message(chat_id, "assistant", task["answer"])


# ── HTML ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "ui" / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ИРУ v3.3 — UI не найден</h1>")


# ── AUTH API ─────────────────────────────────────────────────────────────

@app.post("/api/auth")
async def auth(body: AuthRequest):
    """Авторизация по токену."""
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
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    users = list_users()
    return {"status": "ok", "users": users}


@app.post("/api/admin/users")
async def admin_create_user(body: CreateUserRequest, request: Request):
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    new_user = create_user(body.name)
    return {"status": "ok", "user": new_user}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    ok = delete_user(user_id)
    return {"status": "ok" if ok else "error", "deleted": ok}


# ── DEVICES API ──────────────────────────────────────────────────────────

@app.get("/api/devices")
async def get_devices_api(request: Request):
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
    user = get_current_user(request)
    title = body.title.strip() or "Новый чат"
    chat = create_chat(user["id"], title)
    return {"status": "ok", "chat": chat}


@app.get("/api/chats")
async def api_list_chats(request: Request):
    user = get_current_user(request)
    chats = list_chats(user["id"])
    return {"status": "ok", "chats": chats}


@app.get("/api/chats/{chat_id}/messages")
async def api_get_messages(chat_id: int, request: Request):
    user = get_current_user(request)
    chat = get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    msgs = get_messages(chat_id, limit=50)
    return {"status": "ok", "messages": msgs}


@app.patch("/api/chats/{chat_id}")
async def api_rename_chat(chat_id: int, body: RenameChatRequest, request: Request):
    user = get_current_user(request)
    ok = update_chat_title(chat_id, user["id"], body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"status": "ok"}


@app.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: int, request: Request):
    user = get_current_user(request)
    ok = delete_chat(chat_id, user["id"])
    return {"status": "ok" if ok else "error", "deleted": ok}


# ── COMMAND API ──────────────────────────────────────────────────────────

@app.post("/command")
async def direct_command(cmd: DirectCommand, request: Request):
    """Прямая команда агенту (без LLM). Синхронная."""
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
    """
    NL-команда → фоновая задача.
    Возвращает task_id сразу, не дожидаясь выполнения.
    Поддерживает broadcast (на все устройства) и список device_ids.
    """
    user = get_current_user(request)
    cleanup_old_tasks()

    # Определить целевые устройства
    user_devs = get_user_devices(user["id"])

    if cmd.broadcast:
        # Все устройства пользователя
        target_ids = list(user_devs.keys())
    elif cmd.device_ids:
        # Конкретные устройства
        target_ids = [did for did in cmd.device_ids if did in user_devs]
    else:
        # Одно устройство (как раньше)
        if cmd.device_id not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено или нет доступа"}
        target_ids = [cmd.device_id]

    if not target_ids:
        return {"status": "error", "error": "Нет доступных устройств"}

    # Создать/определить чат
    chat_id = cmd.chat_id
    if not chat_id:
        title = cmd.message[:50].strip() or "Новый чат"
        chat = create_chat(user["id"], title)
        chat_id = chat["id"]
    else:
        chat = get_chat(chat_id, user["id"])
        if not chat:
            return {"status": "error", "error": "Чат не найден"}

    # Сохранить сообщение пользователя
    add_message(chat_id, "user", cmd.message)

    # Создать задачу
    task_id = str(uuid.uuid4())[:12]
    tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": chat_id,
        "message": cmd.message,
        "device_ids": target_ids,
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "created_at": time.time(),
    }

    # Запустить в фоне
    asyncio.create_task(run_nl_task(task_id, user["id"], cmd.message, target_ids, chat_id))

    return {
        "status": "ok",
        "task_id": task_id,
        "chat_id": chat_id,
        "device_ids": target_ids,
    }


# ── TASKS API ────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def api_list_tasks(request: Request):
    """Список задач текущего пользователя."""
    user = get_current_user(request)
    cleanup_old_tasks()
    user_tasks = [t for t in tasks.values() if t["user_id"] == user["id"]]
    user_tasks.sort(key=lambda t: t["created_at"], reverse=True)
    return {
        "status": "ok",
        "tasks": [{
            "task_id": t["task_id"],
            "chat_id": t["chat_id"],
            "message": t["message"][:80],
            "device_ids": t["device_ids"],
            "status": t["status"],
            "answer": t.get("answer"),
            "commands": t.get("commands"),
            "created_at": t["created_at"],
        } for t in user_tasks[:20]],
    }


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str, request: Request):
    """Статус конкретной задачи."""
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {
        "status": "ok",
        "task": {
            "task_id": task["task_id"],
            "chat_id": task["chat_id"],
            "message": task["message"],
            "device_ids": task["device_ids"],
            "status": task["status"],
            "answer": task.get("answer"),
            "commands": task.get("commands"),
            "results": task.get("results", {}),
            "created_at": task["created_at"],
        }
    }


# ── DOWNLOAD API ─────────────────────────────────────────────────────────

@app.get("/api/download/{token}")
async def download_file(token: str):
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

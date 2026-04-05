from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any
import asyncio
import uuid
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
from fastapi.responses import StreamingResponse
import base64
import io

app = FastAPI()
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = Path(__file__).parent / "static" / "index.html"
    return index_path.read_text(encoding="utf-8")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

device_queues: Dict[str, asyncio.Queue] = {}
device_sockets: Dict[str, WebSocket] = {}

# сюда складываем результаты по id команд
pending_results: Dict[str, asyncio.Future] = {}


class Command(BaseModel):
    id: str | None = None
    device_id: str
    action: str
    params: Dict[str, Any] = {}


@app.post("/command")
async def send_command(cmd: Command):
    """Положить команду в очередь для девайса и дождаться результата."""
    if cmd.id is None:
        cmd.id = f"cmd-{uuid.uuid4().hex[:8]}"

    q = device_queues.get(cmd.device_id)
    if q is None:
        return {"status": "error", "error": "device_offline"}

    # создаём future под этот id
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    pending_results[cmd.id] = fut

    # отправляем команду в очередь
    await q.put(cmd.dict())

    try:
        # ждём результат от агента (таймаут, например, 10 сек)
        result = await asyncio.wait_for(fut, timeout=10.0)
        return {"status": "ok", "command_id": cmd.id, "result": result}
    except asyncio.TimeoutError:
        pending_results.pop(cmd.id, None)
        return {"status": "error", "error": "timeout", "command_id": cmd.id}

async def send_internal_command(device_id: str, action: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
    # почти то же, что send_command, но без HTTP-обёртки
    cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
    q = device_queues.get(device_id)
    if q is None:
        raise RuntimeError("device_offline")

    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    pending_results[cmd_id] = fut

    cmd_payload = {
        "id": cmd_id,
        "device_id": device_id,
        "action": action,
        "params": params,
    }
    await q.put(cmd_payload)

    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        pending_results.pop(cmd_id, None)
        raise TimeoutError("timeout")

@app.get("/files/{token}")
async def download_file(token: str, device_id: str = "PC_HOME"):
    try:
        # просим агента отдать содержимое файла по токену
        payload = await send_internal_command(
            device_id=device_id,
            action="get_file_content",
            params={"token": token},
        )
    except RuntimeError:
        return {"status": "error", "error": "device_offline"}
    except TimeoutError:
        return {"status": "error", "error": "timeout"}

    # payload — это то, что агент положил в "payload" при type=result
    if payload.get("status") != "ok":
        return {"status": "error", "error": payload.get("error", "unknown")}

    data = payload.get("result") or {}
    b64 = data.get("data_base64")
    if not b64:
        return {"status": "error", "error": "no_data"}

    file_bytes = base64.b64decode(b64.encode("ascii"))
    filename = data.get("name", "file.bin")
    content_type = data.get("content_type", "application/octet-stream")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@app.websocket("/ws/{device_id}")
async def device_ws(ws: WebSocket, device_id: str):
    await ws.accept()
    q = asyncio.Queue()
    device_queues[device_id] = q
    device_sockets[device_id] = ws
    print(f"[server] device {device_id} connected")

    try:
        while True:
            # 1. если в очереди есть команда — отправляем её агенту
            try:
                cmd = q.get_nowait()
                await ws.send_json({"type": "command", "payload": cmd})
            except asyncio.QueueEmpty:
                pass

            # 2. пробуем прочитать сообщение от агента с небольшим таймаутом
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                # ничего не пришло — просто крутим цикл дальше
                await asyncio.sleep(0.05)
                continue

            # 3. обрабатываем сообщение от агента
            if isinstance(msg, dict) and msg.get("type") == "result":
                payload = msg["payload"]
                cmd_id = payload.get("id")
                fut = pending_results.get(cmd_id)
                if fut is not None and not fut.done():
                    fut.set_result(payload)
                    pending_results.pop(cmd_id, None)
                print(f"[server] result from {device_id}: {payload}")

    except WebSocketDisconnect:
        print(f"[server] device {device_id} disconnected")
    finally:
        device_queues.pop(device_id, None)
        device_sockets.pop(device_id, None)


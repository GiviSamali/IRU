"""Critical reconnect ownership/receipt checks; no live server or agent required."""
import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect
from server.routers import ws as routes


class Socket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.reading = asyncio.Event()
        self.closed = []

    async def accept(self):
        pass

    async def close(self, **kwargs):
        # Model a delayed old-socket close: buffered frames may still arrive.
        self.closed.append(kwargs)

    async def receive_text(self):
        self.reading.set()
        message = await self.messages.get()
        if message is None:
            raise WebSocketDisconnect()
        return json.dumps(message)


@pytest.mark.parametrize("late_type", ["register", "result"])
def test_reconnect_retires_old_pending_and_rejects_old_frames(monkeypatch, late_type):
    devices = {}
    monkeypatch.setattr(routes, "devices", devices)
    monkeypatch.setattr(routes, "get_user_by_token", lambda token: {"id": 1, "name": "test"})
    monkeypatch.setattr(routes, "_is_admin", lambda user: True)
    monkeypatch.setattr(routes, "add_audit_log", lambda *args: None)
    saved = []
    monkeypatch.setattr(routes, "upsert_device_profile", lambda *args: saved.append(args))

    async def scenario():
        key = routes._dk(1, "same")
        foreign_key = routes._dk(2, "same")
        foreign_future = asyncio.get_running_loop().create_future()
        foreign = {"pending": {"shared-id": foreign_future}, "ws": object(), "user_id": 2}
        devices[foreign_key] = foreign
        old_ws, new_ws = Socket(), Socket()
        handlers = []
        try:
            old_task = asyncio.create_task(routes.websocket_agent(old_ws, "same", "token"))
            handlers.append(old_task)
            await asyncio.wait_for(old_ws.reading.wait(), 1)
            old = devices[key]
            old_future = asyncio.get_running_loop().create_future()
            old["pending"]["shared-id"] = old_future
            new_task = asyncio.create_task(routes.websocket_agent(new_ws, "same", "token"))
            handlers.append(new_task)
            await asyncio.wait_for(new_ws.reading.wait(), 1)
            current = devices[key]
            assert current is not old
            assert old_future.done()
            assert old_future.result()["error"].startswith("AGENT_DISCONNECTED:")
            assert old["pending"] == {}
            assert old_ws.closed[0]["reason"] == "agent_reconnected"
            current_future = asyncio.get_running_loop().create_future()
            current["pending"]["shared-id"] = current_future
            payload = ({"hostname": "stale-host"} if late_type == "register" else
                       {"id": "shared-id", "status": "ok", "result": {"stale": True}})
            await old_ws.messages.put({"type": late_type, "payload": payload})
            await asyncio.wait_for(old_task, 1)
            assert devices[key] is current
            assert current["info"] == {}
            assert not current_future.done()
            assert saved == []
            assert devices[foreign_key] is foreign
            assert not foreign_future.done()
            await new_ws.messages.put({"type": "result", "payload": {
                "id": "shared-id", "status": "ok", "result": {"fresh": True},
            }})
            assert await asyncio.wait_for(current_future, 1) == {"fresh": True}
            remaining = asyncio.get_running_loop().create_future()
            current["pending"]["remaining"] = remaining
            await new_ws.messages.put(None)
            await asyncio.wait_for(new_task, 1)
            assert remaining.result()["error"].startswith("AGENT_DISCONNECTED:")
            assert key not in devices
            assert devices[foreign_key] is foreign
            assert not foreign_future.done()
        finally:
            for handler in handlers:
                if not handler.done():
                    handler.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(scenario())

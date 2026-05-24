from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import websockets

from .actions import ACTIONS, collect_system_info
from .local_state import load_device_passport_cache
from .state import AgentState

_PARAM_LOG_LIMIT = 180


def build_registration_payload(device_id: str, agent_version: str) -> dict:
    system_info = collect_system_info(device_id=device_id)
    cached_passport = load_device_passport_cache()
    if cached_passport:
        system_info["cached_passport"] = cached_passport
        system_info["activation_summary"] = cached_passport.get("activation_summary") or {}
        system_info["runtime_summary"] = cached_passport.get("runtime_summary") or {}
        system_info["state_snapshot_summary"] = cached_passport.get("state_snapshot_summary") or {}
        system_info["hardware_summary"] = cached_passport.get("hardware_summary") or {}
    system_info["agent_version"] = agent_version
    return system_info


class AgentRuntime:
    def __init__(
        self,
        config: dict,
        agent_version: str,
        logger: logging.Logger,
        state: AgentState,
    ) -> None:
        self._config = dict(config)
        self._agent_version = agent_version
        self._logger = logger
        self._state = state
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._ws_lock = threading.Lock()

    def current_config(self) -> dict:
        return dict(self._config)

    def update_config(self, config: dict) -> None:
        self._config = dict(config)
        self._state.set_config(
            device_id=self._config.get("device_id", ""),
            server_url=self._config.get("server_url", ""),
        )
        self.restart()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_thread, name="IRUAgentRuntime", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop_event.set()
        self._close_websocket(reason="stop requested")
        if wait and self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=8)

    def restart(self) -> None:
        self.stop(wait=True)
        self.start()

    def request_reconnect(self) -> None:
        if not (self._thread and self._thread.is_alive()):
            self.start()
            return
        self._logger.info("[agent] manual reconnect requested")
        self._close_websocket(reason="manual reconnect")

    def run_headless(self) -> None:
        self.start()
        try:
            while self._thread and self._thread.is_alive():
                self._thread.join(timeout=0.5)
        except KeyboardInterrupt:
            self._logger.info("[agent] keyboard interrupt, stopping")
            self.stop(wait=True)

    def _run_thread(self) -> None:
        asyncio.run(self._run_async())

    def _close_websocket(self, reason: str) -> None:
        with self._ws_lock:
            loop = self._loop
            ws = self._ws
        if not loop or not ws or loop.is_closed():
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.close(reason=reason), loop)
            fut.result(timeout=3)
        except Exception as exc:
            self._logger.debug("[agent] websocket close request failed: %s", exc)

    def _safe_connection_target(self, server_url: str, device_id: str) -> str:
        return f"{server_url}/ws/{device_id}?user_token=***"

    def _preview_text(self, value: str, limit: int = _PARAM_LOG_LIMIT) -> str:
        text = " ".join(str(value).split())
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _format_params_for_log(self, action_name: str, params: dict) -> str:
        if action_name == "execute_cmd":
            command_preview = self._preview_text(params.get("command", ""))
            timeout = params.get("timeout", 30)
            shell = params.get("shell", "auto")
            return f"command='{command_preview}', timeout={timeout}, shell={shell}"

        if action_name == "write_content":
            content = str(params.get("content", ""))
            return (
                f"path='{params.get('path', '')}', append={bool(params.get('append'))}, "
                f"encoding={params.get('encoding', 'utf-8')}, content_len={len(content)}"
            )

        if action_name == "get_file_content":
            return f"path='{params.get('path', '')}'"

        if action_name == "list_dir":
            return f"path='{params.get('path', '') or '<desktop>'}'"

        raw = json.dumps(params, ensure_ascii=False, default=str)
        return self._preview_text(raw)

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        config = self._config
        device_id = config["device_id"]
        server_url = config["server_url"].rstrip("/")
        user_token = config["user_token"]
        ws_url = f"{server_url}/ws/{device_id}?user_token={user_token}"

        self._logger.info(
            "[agent] device=%s, connecting to %s",
            device_id,
            self._safe_connection_target(server_url, device_id),
        )
        system_info = build_registration_payload(device_id, self._agent_version)
        self._logger.info(
            "[agent] system info collected: cpu=%s, ram=%sGB, disks=%s",
            system_info.get("cpu", "?"),
            system_info.get("ram_gb", "?"),
            len(system_info.get("disks", [])),
        )

        while not self._stop_event.is_set():
            self._state.mark_connecting()
            try:
                async with websockets.connect(
                    ws_url,
                    ping_interval=45,
                    ping_timeout=120,
                    open_timeout=30,
                    close_timeout=10,
                    max_size=2**23,
                ) as ws:
                    with self._ws_lock:
                        self._ws = ws
                    self._state.mark_connected()
                    self._logger.info("[agent] connected")

                    await ws.send(json.dumps({"type": "register", "payload": system_info}))

                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        data = json.loads(raw)
                        if data.get("type") != "command":
                            continue

                        payload = await self._handle_command(data.get("payload", {}))
                        await ws.send(json.dumps({"type": "result", "payload": payload}))

            except websockets.ConnectionClosed as exc:
                if self._stop_event.is_set():
                    break
                reason = f"code={exc.code}, reason={exc.reason!r}"
                self._state.mark_disconnected(reason)
                self._logger.warning("[agent] websocket closed: %s", reason)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                reason = f"{type(exc).__name__}: {exc}"
                self._state.mark_disconnected(reason)
                self._logger.warning("[agent] disconnected: %s", reason)
            finally:
                with self._ws_lock:
                    self._ws = None

            if not self._stop_event.is_set():
                await asyncio.sleep(3)

        self._state.mark_disconnected("stopped")
        self._logger.info("[agent] runtime stopped")

    async def _handle_command(self, cmd: dict) -> dict:
        cmd_id = cmd.get("id", "")
        action_name = cmd.get("action", "")
        params = cmd.get("params", {})

        self._logger.info(
            "[agent] executing action=%s %s",
            action_name,
            self._format_params_for_log(action_name, params),
        )
        try:
            if action_name == "agent.shutdown":
                self._stop_event.set()
                return {"id": cmd_id, "status": "ok", "result": {"ack": True, "action": "agent.shutdown"}}
            func = ACTIONS.get(action_name)
            if func is None:
                raise ValueError(f"Неизвестное действие: {action_name}")
            result = await asyncio.to_thread(func, **params)
            if isinstance(result, dict) and result.get("error"):
                self._logger.warning("[agent] action=%s returned error=%s", action_name, result["error"])
            return {"id": cmd_id, "status": "ok", "result": result}
        except Exception as exc:
            self._logger.exception("[agent] action=%s failed", action_name)
            return {"id": cmd_id, "status": "error", "error": str(exc)}

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from threading import Lock


def _now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class AgentSnapshot:
    version: str
    device_id: str
    server_url: str
    config_path: str
    logs_dir: str
    log_path: str
    status: str = "disconnected"
    last_connected_at: str = ""
    last_disconnect_reason: str = ""
    last_update_check: str = "not_checked"
    update_state: str = ""
    update_progress: int = -1
    update_detail: str = ""
    last_error: str = ""


class AgentState:
    def __init__(self, snapshot: AgentSnapshot):
        self._snapshot = snapshot
        self._lock = Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self._snapshot)

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._snapshot, key):
                    setattr(self._snapshot, key, value)

    def set_config(self, device_id: str, server_url: str) -> None:
        self.update(device_id=device_id, server_url=server_url)

    def mark_connecting(self) -> None:
        self.update(status="connecting")

    def mark_connected(self) -> None:
        self.update(
            status="connected",
            last_connected_at=_now_text(),
            last_disconnect_reason="",
            last_error="",
        )

    def mark_disconnected(self, reason: str = "") -> None:
        self.update(status="disconnected", last_disconnect_reason=reason, last_error=reason)

    def mark_config_error(self, reason: str) -> None:
        self.update(status="config_error", last_error=reason)

    def set_update_status(
        self,
        message: str,
        state: str = "",
        progress: int | None = None,
        detail: str | None = None,
    ) -> None:
        payload = {"last_update_check": message, "update_state": state}
        if state:
            payload["status"] = state
        if progress is not None:
            payload["update_progress"] = progress
        if detail is not None:
            payload["update_detail"] = detail
        self.update(**payload)

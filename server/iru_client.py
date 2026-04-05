# iru_client.py
import httpx

class IRUClient:
    def __init__(self, base_url: str):
        # например, base_url = "http://192.168.0.10:8000"
        self.base_url = base_url.rstrip("/")

    def send_command(self, device_id: str, action: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        payload = {
            "device_id": device_id,
            "action": action,
            "params": params or {},
        }
        resp = httpx.post(f"{self.base_url}/command", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

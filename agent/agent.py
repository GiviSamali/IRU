# agent/agent.py
import json
import asyncio
import websockets
from pathlib import Path
from actions import ACTIONS

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data["device_id"], data["server_url"]


async def run_agent():
    device_id, server_url = load_config()
    print(f"[agent] starting for {device_id}, server={server_url}")

    async for ws in websockets.connect(server_url):
        try:
            print("[agent] connected to server")
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                if data.get("type") == "command":
                    cmd = data["payload"]
                    print(f"[agent] got command: {cmd}")
                    cmd_id = cmd["id"]
                    action_name = cmd["action"]
                    params = cmd.get("params", {})
                    try:
                        func = ACTIONS[action_name]
                        result = func(**params)
                        payload = {
                            "id": cmd_id,
                            "status": "ok",
                            "result": result,
                        }
                    except Exception as e:
                        payload = {
                            "id": cmd_id,
                            "status": "error",
                            "error": str(e),
                        }
                    await ws.send(json.dumps({"type": "result", "payload": payload}))
        except Exception as e:
            print(f"[agent] connection error: {e}, reconnecting in 3s")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run_agent())

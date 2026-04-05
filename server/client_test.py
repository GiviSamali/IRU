# server/client_test.py
from iru_client import IRUClient

iru = IRUClient("http://127.0.0.1:8000")

resp = iru.send_command(
    device_id="PC_HOME",
    action="open_app",
    params={"name": "Steam"},
)
print(resp)

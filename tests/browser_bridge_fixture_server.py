"""Local-only test harness for the real extension -> production bridge protocol.

No LLM, real user credentials, filesystem operations or agents are involved.
The native extension and production HTTP/WS routers are used unchanged.
"""
import argparse
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid

ACCOUNT_TOKEN = "browser-fixture-account-token"  # Test fixture, never a production secret.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    with tempfile.TemporaryDirectory(prefix="iru-browser-fixture-") as directory:
        os.environ["IRU_DB_PATH"] = str(Path(directory)/"fixture.sqlite3")
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        from server import browser_bridge as bridge, database as db
        from server.api_support import get_current_user
        from server.routers.browser import router
        from server.runtime_state import devices
        # init_db emits a generated admin credential; do not print it from a test.
        with contextlib.redirect_stdout(io.StringIO()):
            db.init_db()
        with db.get_db() as conn:
            conn.execute("UPDATE users SET token=? WHERE id=1", (ACCOUNT_TOKEN,))
        db.upsert_device_profile("givi", 1, {"hostname":"fixture", "os":"Windows", "username":"FixtureUser"})
        devices["1:givi"]={"user_id":1,"ws":object(),"info":{"hostname":"fixture"}}
        bridge.init_browser_bridge(restart=True)
        app=FastAPI()
        app.include_router(router)
        app.mount("/fixture/pages",StaticFiles(directory=str(repo/"tests"/"browser-fixtures")),name="fixture_pages")

        @app.get("/fixture/ready")
        async def ready():
            return {"status":"ok","device_id":"givi"}

        @app.post("/fixture/action")
        async def action(request: Request):
            user=get_current_user(request)
            if user["id"]!=1:
                raise HTTPException(403,"fixture_owner_required")
            body=await request.json()
            if not isinstance(body,dict) or set(body)-{"operation","params","authorization","task_id"}:
                raise HTTPException(400,"invalid_fixture_action")
            auth=body.get("authorization") or {}
            if not isinstance(auth,dict) or set(auth)-{"external_action"}:
                raise HTTPException(400,"invalid_fixture_authorization")
            return await bridge.execute_browser_action(1,body.get("task_id") or uuid.uuid4().hex,"givi",body.get("operation"),body.get("params") or {},external_action=auth.get("external_action") is True)

        sock=socket.socket()
        sock.bind(("127.0.0.1",args.port))
        port=sock.getsockname()[1]
        print("IRU_BROWSER_FIXTURE_PORT="+str(port),flush=True)
        server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="error",access_log=False,lifespan="off"))
        try:
            server.run(sockets=[sock])
        finally:
            sock.close()


if __name__=="__main__":
    main()

"""One managed Python interpreter per task on this Agent connection."""
import asyncio
import json
import os
import subprocess

from .actions import _iru_home, _runtime_venv_python


_RUNNER = r"""
import contextlib, io, json, sys, traceback

class Output(io.StringIO):
    def write(self, text):
        remaining = max(0, 16000 - self.tell())
        super().write(text[:remaining])
        return len(text)

scope = {"__name__": "__main__"}
requests = sys.stdin.buffer
protocol = sys.stdout
sys.stdin = io.StringIO()
for line in requests:
    stdout, stderr = Output(), Output()
    error = None
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(json.loads(line), "<python.run>", "exec"), scope)
    except BaseException as exc:
        error = type(exc).__name__
        traceback.print_exc(file=stderr)
    print(json.dumps({"status": "failed" if error else "success", "error": error,
                      "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}),
          file=protocol, flush=True)
"""


class PythonSessions:
    def __init__(self):
        self.processes = {}

    async def close(self, task_id):
        process = self.processes.pop(task_id, None)
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def close_all(self):
        for task_id in list(self.processes):
            await self.close(task_id)

    async def run(self, task_id, code):
        if not isinstance(code, str) or not code.strip() or len(code) > 32000:
            return {"status": "failed", "dispatched": False, "error": "invalid_python_code"}
        process = self.processes.get(task_id)
        if process is None:
            home = _iru_home()
            executable = _runtime_venv_python(home)
            if not executable.is_file():
                return {"status": "failed", "dispatched": False, "error": "iru_python_unavailable"}
            try:
                process = await asyncio.create_subprocess_exec(
                    str(executable), "-I", "-u", "-X", "utf8", "-c", _RUNNER,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, cwd=str(home),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    limit=512 * 1024,
                )
            except OSError:
                return {"status": "failed", "dispatched": False, "error": "iru_python_start_failed"}
            self.processes[task_id] = process

        async def exchange():
            process.stdin.write(json.dumps(code).encode("ascii") + b"\n")
            await process.stdin.drain()
            return json.loads(await process.stdout.readline())

        try:
            result = await asyncio.wait_for(exchange(), timeout=20)
            if not isinstance(result, dict) or result.get("status") not in {"success", "failed"}:
                raise ValueError("invalid_python_reply")
            return result
        except asyncio.CancelledError:
            await self.close(task_id)
            raise
        except (OSError, ValueError, asyncio.TimeoutError):
            await self.close(task_id)
            return {"status": "unknown", "error": "python_outcome_unknown"}

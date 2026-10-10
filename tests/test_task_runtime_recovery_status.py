import asyncio
import time
import pytest

from server import task_runtime


@pytest.mark.parametrize("receipt_status", ["completed_with_recovery", "partial", "unknown"])
def test_pipeline_receipt_completed_with_recovery_sets_top_level_task_status(monkeypatch, receipt_status):
    task_id = "recovery-status"
    full_device_id = "1:device-a"
    task_runtime.tasks[task_id] = {
        "task_id": task_id,
        "user_id": 1,
        "chat_id": 1,
        "message": "create files",
        "device_ids": [full_device_id],
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "modes": {"pipeline": True},
        "created_at": time.time(),
    }
    task_runtime.devices[full_device_id] = {
        "user_id": 1,
        "info": {"hostname": "alpha", "os": "Windows", "os_version": "11"},
        "pending": {},
    }

    async def _process_nl_command(**kwargs):
        return {
            "answer": "done with recovery",
            "commands": [],
            "tasks": [],
            "task_receipt": {"task_status": receipt_status},
        }

    async def _probe(**kwargs):
        return None

    monkeypatch.setattr(task_runtime, "_probe_python_toolchain_if_needed", _probe)
    monkeypatch.setattr(task_runtime, "process_nl_command", _process_nl_command)
    monkeypatch.setattr(task_runtime, "get_user_devices", lambda user_id: {full_device_id: task_runtime.devices[full_device_id]})
    monkeypatch.setattr(task_runtime, "get_messages", lambda chat_id, limit=50: [{"role": "user", "content": "create files"}])
    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id, **kw: None)
    monkeypatch.setattr(task_runtime, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "add_training_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "enforce_trusted_answer", lambda answer, commands: answer)

    try:
        asyncio.run(task_runtime.run_nl_task(task_id, 1, "create files", [full_device_id], 1))

        assert task_runtime.tasks[task_id]["status"] == receipt_status
        assert task_runtime.tasks[task_id]["task_receipt"]["task_status"] == receipt_status
    finally:
        task_runtime.tasks.pop(task_id, None)
        task_runtime.devices.pop(full_device_id, None)


def test_runtime_preserves_grounded_markdown_before_report(monkeypatch):
    task_id = "recovery-status"
    full_device_id = "1:device-a"
    task_runtime.tasks[task_id] = {
        "task_id": task_id,
        "user_id": 1,
        "chat_id": 1,
        "message": "create files",
        "device_ids": [full_device_id],
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "modes": {"pipeline": True},
        "created_at": time.time(),
    }
    task_runtime.devices[full_device_id] = {
        "user_id": 1,
        "info": {"hostname": "alpha", "os": "Windows", "os_version": "11"},
        "pending": {},
    }

    async def _process_nl_command(**kwargs):
        return {
            "answer": "**Observed:** IRU",
            "commands": [
                {"tool_name":"execute_cmd","step_id":"step_1","status":"success","result":{"returncode":0,"stdout":"OK: IRU"}},
                {"tool_name":"answer.text","status":"terminal","result":{"answer_type":"grounded_report","text":"**Observed:** IRU","basis":["step_1"],"self_check":{"depends_on_current_external_state":True,"claims_completed_action":False,"has_sufficient_evidence":True,"missing_evidence_question":""}}}],
            "tasks": [],
            "task_receipt": {"task_status": "completed"},
        }

    async def _probe(**kwargs):
        return None

    monkeypatch.setattr(task_runtime, "_probe_python_toolchain_if_needed", _probe)
    monkeypatch.setattr(task_runtime, "process_nl_command", _process_nl_command)
    monkeypatch.setattr(task_runtime, "get_user_devices", lambda user_id: {full_device_id: task_runtime.devices[full_device_id]})
    monkeypatch.setattr(task_runtime, "get_messages", lambda chat_id, limit=50: [{"role": "user", "content": "create files"}])
    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id, **kw: None)
    monkeypatch.setattr(task_runtime, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "add_training_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "enforce_trusted_answer", lambda answer, commands: answer)

    try:
        asyncio.run(task_runtime.run_nl_task(task_id, 1, "create files", [full_device_id], 1))

        assert task_runtime.tasks[task_id]["answer"] == "**Observed:** IRU"
        assert task_runtime.has_grounded_terminal_answer(task_runtime.tasks[task_id]["answer"],task_runtime.tasks[task_id]["commands"])
    finally:
        task_runtime.tasks.pop(task_id, None)
        task_runtime.devices.pop(full_device_id, None)

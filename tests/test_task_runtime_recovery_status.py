import asyncio
import time

from server import task_runtime


def test_pipeline_receipt_completed_with_recovery_sets_top_level_task_status(monkeypatch):
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
            "task_receipt": {"task_status": "completed_with_recovery"},
        }

    async def _probe(**kwargs):
        return None

    monkeypatch.setattr(task_runtime, "_probe_python_toolchain_if_needed", _probe)
    monkeypatch.setattr(task_runtime, "process_nl_command", _process_nl_command)
    monkeypatch.setattr(task_runtime, "get_user_devices", lambda user_id: {full_device_id: task_runtime.devices[full_device_id]})
    monkeypatch.setattr(task_runtime, "get_messages", lambda chat_id, limit=50: [{"role": "user", "content": "create files"}])
    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id: None)
    monkeypatch.setattr(task_runtime, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "add_training_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_runtime, "enforce_trusted_answer", lambda answer, commands: answer)

    try:
        asyncio.run(task_runtime.run_nl_task(task_id, 1, "create files", [full_device_id], 1))

        assert task_runtime.tasks[task_id]["status"] == "completed_with_recovery"
        assert task_runtime.tasks[task_id]["task_receipt"]["task_status"] == "completed_with_recovery"
    finally:
        task_runtime.tasks.pop(task_id, None)
        task_runtime.devices.pop(full_device_id, None)

import asyncio
import json

import httpx

from server.controller_pipeline import (
    build_pipeline_worker_context,
    pipeline_worker_prompt,
    process_pipeline_subagents,
    run_pipeline_worker,
)


def _make_completion_fn(responses, captured_messages=None):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        if captured_messages is not None:
            captured_messages.append(kwargs.get("messages"))
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _chat_completion_request_fn


def _execute_call(call_id, command, device_id=None):
    args = {"command": command}
    if device_id:
        args["device_id"] = device_id
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps(args),
        },
    }


def _shared_context(device_id="device-1"):
    return {
        "current_device_id": device_id,
        "current_hostname": "devbox",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "other_devices_summary": "",
        "target_device_id": device_id,
        "os_rules": "",
        "current_datetime_msk": "2026-05-02 12:00",
    }


def _run_worker_case(responses, send_command_fn=None, step=None):
    async def _noop_send_command_fn(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    def _noop_get_file_link_fn(device_id, file_path):
        return "/api/download/mock"

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=_shared_context(),
                overall_goal="goal",
                step=step or {"title": "step", "instruction": "do it", "device_id": "device-1"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=send_command_fn or _noop_send_command_fn,
                get_file_link_fn=_noop_get_file_link_fn,
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_make_completion_fn(responses),
                worker_tools=[],
            )

    return asyncio.run(_run())


def test_pipeline_worker_does_not_stop_on_many_execute_commands():
    n = 20
    calls = [_execute_call(f"call-{idx}", f"whoami {idx}") for idx in range(n + 1)]
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": calls},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert result["status"] == "ok"
    assert len(executed) == n + 1
    assert "budget_guard" not in [c.get("action") for c in result.get("commands", [])]


def test_pipeline_worker_does_not_stop_repeated_similar_execute_commands():
    calls = [
        _execute_call("call-1", "Start-Process calc.exe"),
        _execute_call("call-2", 'Start-Process -FilePath "calc.exe"'),
        _execute_call("call-3", "Start-Process calc"),
        _execute_call("call-4", "Start-Process -FilePath calc.exe"),
    ]
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": calls},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert executed == [
        "Start-Process calc.exe",
        'Start-Process -FilePath "calc.exe"',
        "Start-Process calc",
        "Start-Process -FilePath calc.exe",
    ]
    assert "budget_guard" not in [c.get("action") for c in result.get("commands", [])]
    assert result["status"] == "ok"


def test_pipeline_worker_single_execute_command_is_not_blocked():
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append((device_id, action, params["command"]))
        return {"returncode": 0, "stdout": "user", "stderr": ""}

    result = _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", "whoami")]},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert result["status"] == "ok"
    assert executed == [("device-1", "execute_cmd", "whoami")]
    assert result["answer"] == "ok"


def test_pipeline_worker_ignores_llm_device_override():
    seen_devices = []

    async def _send_command_fn(device_id, action, params):
        seen_devices.append(device_id)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", "whoami", device_id="device-2")]},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert seen_devices == ["device-1"]


def _build_device_a_worker_context(monkeypatch):
    all_devices = {
        "device-a": {
            "info": {
                "hostname": "alpha",
                "os": "Windows",
                "os_version": "11",
                "desktop_path": r"C:\Users\alpha\Desktop",
            }
        },
        "device-b": {
            "info": {
                "hostname": "bravo",
                "os": "Windows",
                "os_version": "10",
                "machine_guid": "guid-b",
                "desktop_path": r"D:\bravo\Desktop",
                "home": r"D:\bravo",
                "project_root": r"D:\bravo\secret-project",
                "recent_commands": "type D:\\bravo\\secret.txt",
                "output": "secret-output",
            }
        },
    }

    def _profile(device_id):
        if device_id == "device-a":
            return {
                "device_id": "device-a",
                "hostname": "alpha",
                "os": "Windows",
                "os_version": "11",
                "username": "alice",
                "desktop_path": r"C:\Users\alpha\Desktop",
                "machine_guid": "guid-a",
            }
        if device_id == "device-b":
            return {
                "device_id": "device-b",
                "username": "bob",
                "desktop_path": r"D:\bravo\Desktop",
                "machine_guid": "guid-b",
            }
        return None

    def _memory(machine_guid, user_id):
        if machine_guid == "guid-a":
            return "target memory only"
        if machine_guid == "guid-b":
            return r"device B memory D:\bravo\secret.txt"
        return ""

    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", _profile)
    monkeypatch.setattr("server.controller_pipeline.build_memory_block", _memory)
    return build_pipeline_worker_context(
        target_device_id="device-a",
        current_device_id="device-a",
        current_device_info=all_devices["device-a"]["info"],
        all_devices=all_devices,
        current_device_profile=_profile("device-a"),
        mem_user_id="user-1",
        windows_rules="windows rules",
        linux_rules="linux rules",
    )


def test_pipeline_worker_context_excludes_other_device_paths_profile_and_memory(monkeypatch):
    shared, machine_guid = _build_device_a_worker_context(monkeypatch)
    prompt = pipeline_worker_prompt(
        shared,
        "goal",
        {"title": "step", "instruction": "do it", "device_id": "device-a"},
        [],
    )

    assert machine_guid == "guid-a"
    for expected in ("device-a", "alpha", "device-b", "bravo"):
        assert expected in prompt
    for forbidden in (
        r"D:\bravo\Desktop",
        r"D:\bravo\secret-project",
        r"D:\bravo\secret.txt",
        "guid-b",
        "device B memory",
        "secret-output",
    ):
        assert forbidden not in prompt


def test_pipeline_worker_llm_messages_do_not_include_other_device_paths(monkeypatch):
    captured_messages = []
    shared, _ = _build_device_a_worker_context(monkeypatch)

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=shared,
                overall_goal="goal",
                step={"title": "step", "instruction": "do it", "device_id": "device-a"},
                completed_steps=[],
                chat_history=[{"role": "assistant", "content": r"use D:\bravo\secret.txt"}],
                send_command_fn=lambda *args, **kwargs: None,
                get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
                machine_guid="guid-a",
                mem_user_id="user-1",
                poll_task_id=None,
                chat_completion_request_fn=_make_completion_fn([{
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }]
                }], captured_messages),
                worker_tools=[],
            )

    asyncio.run(_run())
    combined = "\n".join(msg.get("content", "") for msg in captured_messages[0])

    assert "target_device=device-a" in combined
    for forbidden in (r"D:\bravo\Desktop", r"D:\bravo\secret-project", r"D:\bravo\secret.txt", "guid-b"):
        assert forbidden not in combined


def test_pipeline_completed_steps_mark_other_device_paths_as_informational():
    prompt = pipeline_worker_prompt(
        _shared_context("device-a"),
        "goal",
        {"title": "step", "instruction": "do it", "device_id": "device-a"},
        [{
            "title": "previous",
            "summary": r"created D:\bravo\artifact.txt",
            "device_id": "device-b",
            "hostname": "bravo",
        }],
    )

    assert "OTHER DEVICE device_id=device-b hostname=bravo" in prompt
    assert "informational only" in prompt
    assert "do not reuse paths as target-device paths" in prompt


def test_pipeline_invalid_planner_device_falls_back_to_current_device(monkeypatch, caplog):
    responses = [
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "goal": "goal",
                        "steps": [{
                            "title": "step",
                            "instruction": "run command",
                            "device_id": "missing-device",
                        }],
                    }),
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [_execute_call("call-1", "whoami")]},
            }]
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "worker ok"},
            }]
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "summary ok"},
            }]
        },
    ]
    captured_messages = []
    seen_devices = []

    monkeypatch.setattr("server.controller_pipeline.db.create_task", lambda **kwargs: 1)
    monkeypatch.setattr("server.controller_pipeline.db.update_step", lambda *args, **kwargs: True)
    monkeypatch.setattr("server.controller_pipeline.db.finish_task", lambda *args, **kwargs: True)
    monkeypatch.setattr("server.controller_pipeline.collect_tasks", lambda task_ids: [])
    monkeypatch.setattr("server.controller_pipeline.push_tasks_view", lambda *args, **kwargs: None)
    monkeypatch.setattr("server.controller_pipeline.db.get_device_profile", lambda device_id: None)
    monkeypatch.setattr("server.controller_pipeline.build_memory_block", lambda machine_guid, user_id: "")
    monkeypatch.setattr("server.controller_pipeline.db.add_command_memory", lambda **kwargs: None)

    async def _send_command_fn(device_id, action, params):
        seen_devices.append(device_id)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    async def _run():
        return await process_pipeline_subagents(
            user_message="run command",
            device_id="device-a",
            device_info={"hostname": "alpha", "os": "Windows", "os_version": "11"},
            all_devices={"device-a": {"info": {"hostname": "alpha", "os": "Windows", "os_version": "11"}}},
            send_command_fn=_send_command_fn,
            get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
            chat_history=[],
            user_id=1,
            chat_id=1,
            device_profile=None,
            modes={},
            poll_task_id=None,
            load_llm_config_fn=lambda: {"model": "mock-model", "max_tokens": 1000},
            pick_model_fn=lambda cfg, modes: "mock-model",
            chat_completion_request_fn=_make_completion_fn(responses, captured_messages),
            worker_tools=[],
            windows_rules="windows rules",
            linux_rules="linux rules",
        )

    caplog.set_level("WARNING")
    result = asyncio.run(_run())

    assert result["answer"] == "summary ok"
    assert seen_devices == ["device-a"]
    assert "Invalid pipeline step.device_id=missing-device" in caplog.text
    worker_messages = captured_messages[1]
    assert any("target_device=device-a" in msg.get("content", "") for msg in worker_messages)

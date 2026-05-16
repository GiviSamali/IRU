import asyncio
import json
import re
import time

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_trust import SAFE_DOWNLOAD_LINK_ERROR, enforce_trusted_answer


def _make_completion_fn(responses):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _chat_completion_request_fn


def _run_non_pipeline_case(responses, send_command_fn=None, get_file_link_fn=None, device_id="device-1", modes=None):
    async def _noop_send_command_fn(device_id, action, params):
        return {"ok": True}

    def _noop_get_file_link_fn(device_id, file_path):
        return "/api/download/mock"

    return asyncio.run(
        process_non_pipeline_command(
            user_message="Сделай это",
            device_id=device_id,
            device_info={"os": "Windows", "hostname": "devbox"},
            send_command_fn=send_command_fn or _noop_send_command_fn,
            get_file_link_fn=get_file_link_fn or _noop_get_file_link_fn,
            chat_history=[],
            user_id=None,
            chat_id=None,
            modes=modes or {},
            poll_task_id=None,
            cfg={"model": "mock-model", "max_tokens": 512},
            system_msg="system",
            machine_guid=None,
            mem_user_id=None,
            non_pipeline_tools=[],
            max_iterations=4,
            pick_model_fn=lambda cfg, modes: "mock-model",
            chat_completion_request_fn=_make_completion_fn(responses),
        )
    )


def test_non_pipeline_blocks_fabricated_download_link_without_tool_result():
    result = _run_non_pipeline_case([
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "Файл создан, ссылка https://storage.yandexcloud.net/agent-files/report.txt",
                },
            }]
        }
    ])

    assert result["answer"] == SAFE_DOWNLOAD_LINK_ERROR
    assert "storage.yandexcloud.net" not in result["answer"]


def test_inventory_network_scan_wording_is_replaced():
    expected = "Других подключённых к ИРУ устройств сейчас не вижу."

    assert enforce_trusted_answer("Других устройств в сети не обнаружено.", []) == expected
    assert enforce_trusted_answer("устройств в сети не обнаружено", []) == expected


def test_non_pipeline_uses_only_real_get_file_link_url():
    result = _run_non_pipeline_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "function": {
                                "name": "get_file_link",
                                "arguments": json.dumps({"file_path": r"C:\Temp\report.txt"}),
                            },
                        }],
                    },
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": "Файл создан, ссылка https://storage.yandexcloud.net/agent-files/report.txt",
                    },
                }]
            },
        ],
        get_file_link_fn=lambda device_id, file_path: "/api/download/abc",
    )

    urls = re.findall(r"https?://[^\s<>()\"']+|/api/download/[A-Za-z0-9_-]+", result["answer"])
    assert urls == ["/api/download/abc"]
    assert "storage.yandexcloud.net" not in result["answer"]


def test_non_pipeline_write_content_error_cannot_be_reported_as_success():
    async def _send_command_fn(device_id, action, params):
        assert action == "write_content"
        return {"error": "disk full"}

    result = _run_non_pipeline_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "function": {
                                "name": "write_content",
                                "arguments": json.dumps({"path": r"C:\Temp\report.txt", "content": "hello"}),
                            },
                        }],
                    },
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": "Готово, файл создан.",
                    },
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    answer = result["answer"].lower()
    assert "готово" not in answer
    assert "создан" not in answer
    assert "не удалось" in answer or "ошиб" in answer


def test_non_pipeline_execute_cmd_error_is_reported_as_error():
    async def _send_command_fn(device_id, action, params):
        assert action == "execute_cmd"
        return {"returncode": 1, "stderr": "Access denied", "stdout": ""}

    result = _run_non_pipeline_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "function": {
                                "name": "execute_cmd",
                                "arguments": json.dumps({"command": "mkdir C:\\Temp"}),
                            },
                        }],
                    },
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": "Команда выполнена.",
                    },
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    answer = result["answer"].lower()
    assert "ошиб" in answer or "не удалось" in answer
    assert "выполнена" not in answer


def test_non_pipeline_allows_regular_external_pdf_link_without_get_file_link():
    result = _run_non_pipeline_case([
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "Вот инструкция: https://example.com/manual.pdf",
                },
            }]
        }
    ])

    assert result["answer"] == "Вот инструкция: https://example.com/manual.pdf"


def test_non_pipeline_allows_regular_github_readme_link_without_get_file_link():
    result = _run_non_pipeline_case([
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "Смотри README: https://github.com/example/project/blob/main/README.md",
                },
            }]
        }
    ])

    assert result["answer"] == "Смотри README: https://github.com/example/project/blob/main/README.md"


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


def test_non_pipeline_does_not_stop_on_many_execute_commands():
    n = 20
    calls = [_execute_call(f"call-{idx}", f"whoami {idx}") for idx in range(n + 1)]
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_non_pipeline_case(
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

    assert len(executed) == n + 1
    assert "budget_guard" not in [c.get("action") for c in result.get("commands", [])]
    assert result["answer"] == "ok"


def test_non_pipeline_does_not_stop_repeated_similar_execute_commands():
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

    result = _run_non_pipeline_case(
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
    assert result["answer"] == "ok"


def test_non_pipeline_single_execute_command_is_not_blocked():
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append((device_id, action, params["command"]))
        return {"returncode": 0, "stdout": "user", "stderr": ""}

    result = _run_non_pipeline_case(
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

    assert executed == [("device-1", "execute_cmd", "whoami")]
    assert result["answer"] == "ok"


def test_non_pipeline_execute_cmd_uses_llm_device_override():
    seen_devices = []

    async def _send_command_fn(device_id, action, params):
        seen_devices.append(device_id)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    _run_non_pipeline_case(
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
        device_id="device-1",
    )

    assert seen_devices == ["device-2"]


def test_non_pipeline_broadcast_mode_honors_tool_device_override():
    seen_devices = []

    async def _send_command_fn(device_id, action, params):
        seen_devices.append(device_id)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    _run_non_pipeline_case(
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
        device_id="device-1",
    )

    assert seen_devices == ["device-2"]


def test_chat_broadcast_replays_command_to_all_devices(client, monkeypatch):
    import server.routers.tasks as tasks_router
    import server.runtime_state as runtime_state
    import server.task_runtime as task_runtime
    from server.database import create_user

    user = create_user("broadcast-guard-user")
    auth_response = client.post("/api/auth", json={"token": user["token"]})
    headers = {"Authorization": f"Bearer {auth_response.json()['access_token']}"}

    monkeypatch.setattr(tasks_router, "devices", runtime_state.devices)
    monkeypatch.setattr(task_runtime, "devices", runtime_state.devices)
    monkeypatch.setattr(tasks_router, "tasks", runtime_state.tasks)
    monkeypatch.setattr(task_runtime, "tasks", runtime_state.tasks)
    first_key = f"{user['id']}:device-1"
    second_key = f"{user['id']}:device-2"
    runtime_state.devices[first_key] = {
        "user_id": user["id"],
        "info": {"hostname": "one", "os": "Windows"},
        "pending": {},
    }
    runtime_state.devices[second_key] = {
        "user_id": user["id"],
        "info": {"hostname": "two", "os": "Windows"},
        "pending": {},
    }

    async def fake_classify_task_complexity(message):
        return ("SIMPLE", "")

    async def fake_process_nl_command(**kwargs):
        assert kwargs["device_id"] == "device-1"
        return {
            "answer": "ok",
            "commands": [{
                "action": "execute_cmd",
                "command": "whoami",
                "device_id": "device-1",
                "result": {"returncode": 0, "stdout": "one", "stderr": ""},
            }],
            "tasks": [],
        }

    replayed = []

    async def fake_send_command_to_agent(device_id, action, params, user_id=None, skip_confirm=False):
        replayed.append((device_id, action, params["command"]))
        return {"returncode": 0, "stdout": "two", "stderr": ""}

    monkeypatch.setattr(task_runtime, "classify_task_complexity", fake_classify_task_complexity)
    monkeypatch.setattr(task_runtime, "process_nl_command", fake_process_nl_command)
    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send_command_to_agent)

    created = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "whoami", "broadcast": True},
    )
    assert created.status_code == 200
    assert set(created.json()["device_ids"]) == {first_key, second_key}

    task_id = created.json()["task_id"]
    task = None
    for _ in range(30):
        response = client.get(f"/api/tasks/{task_id}", headers=headers)
        assert response.status_code == 200
        task = response.json()["task"]
        if task["status"] == "done":
            break
        time.sleep(0.05)

    assert task is not None
    assert task["status"] == "done"
    assert replayed == [(second_key, "execute_cmd", "whoami")]
    assert {cmd["device_id"] for cmd in task["commands"]} == {"device-1", second_key}

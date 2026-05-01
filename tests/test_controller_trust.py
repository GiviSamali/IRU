import asyncio
import json
import re

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_trust import SAFE_DOWNLOAD_LINK_ERROR


def _make_completion_fn(responses):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _chat_completion_request_fn


def _run_non_pipeline_case(responses, send_command_fn=None, get_file_link_fn=None):
    async def _noop_send_command_fn(device_id, action, params):
        return {"ok": True}

    def _noop_get_file_link_fn(device_id, file_path):
        return "/api/download/mock"

    return asyncio.run(
        process_non_pipeline_command(
            user_message="Сделай это",
            device_id="device-1",
            device_info={"os": "Windows", "hostname": "devbox"},
            send_command_fn=send_command_fn or _noop_send_command_fn,
            get_file_link_fn=get_file_link_fn or _noop_get_file_link_fn,
            chat_history=[],
            user_id=None,
            chat_id=None,
            modes={},
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

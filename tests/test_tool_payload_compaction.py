import asyncio
import json

from server.controller_non_pipeline import process_non_pipeline_command
from server.run_journal import validate_answer_text_payload, wrap_tool_result_for_llm
from server.tool_payload_compaction import (
    compact_tool_args,
    compact_tool_call_for_history,
    compact_tool_result,
    compact_value,
    sha256_text,
)


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _message(tool_calls=None):
    msg = {"content": ""}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"finish_reason": "tool_calls", "message": msg}]}


def _answer_payload(text: str = "done", basis=None) -> dict:
    return {
        "answer_type": "grounded_report",
        "text": text,
        "basis": basis or ["step_1"],
        "self_check": {
            "depends_on_current_external_state": True,
            "claims_completed_action": True,
            "has_sufficient_evidence": True,
            "missing_evidence_question": "",
        },
    }


def test_compact_value_leaves_short_strings_unchanged():
    assert compact_value("short", max_chars=10) == "short"


def test_compact_value_compacts_long_strings_with_hash_and_previews():
    value = "a" * 5000 + "tail"
    compacted = compact_value(value, max_chars=1000, preview_chars=50)

    assert compacted["omitted"] is True
    assert compacted["kind"] == "large_string"
    assert compacted["chars"] == len(value)
    assert compacted["sha256"] == sha256_text(value)
    assert compacted["preview"] == "a" * 50
    assert compacted["tail_preview"].endswith("tail")


def test_compact_tool_args_compacts_write_content_content():
    content = "x" * 10000
    compacted = compact_tool_args("write_content", {"path": "a.html", "content": content})

    assert compacted["path"] == "a.html"
    assert compacted["content"]["omitted"] is True
    assert compacted["content"]["chars"] == len(content)
    assert compacted["content"]["sha256"] == sha256_text(content)


def test_compact_tool_args_compacts_fs_write_file_content():
    content = "x" * 10000
    compacted = compact_tool_args("fs.write_file", {"path": "a.html", "content": content})

    assert compacted["content"]["omitted"] is True
    assert compacted["content"]["kind"] == "large_string"


def test_compact_tool_args_compacts_fs_patch_file_large_operations():
    replace = "new text" * 2000
    operations = [{"op": "replace", "find": "old", "replace": replace}]
    compacted = compact_tool_args("fs.patch_file", {"path": "a.txt", "operations": operations})

    assert compacted["path"] == "a.txt"
    assert compacted["operations"]["omitted"] is True
    assert compacted["operations"]["kind"] == "large_json"
    assert compacted["operations"]["items"] == 1


def test_compact_tool_args_compacts_execute_cmd_command():
    command = "Write-Output x; " * 1000
    compacted = compact_tool_args("execute_cmd", {"command": command})

    assert compacted["command"]["omitted"] is True
    assert compacted["command"]["sha256"] == sha256_text(command)


def test_compact_tool_result_compacts_stdout_and_stderr():
    stdout = "out" * 3000
    stderr = "err" * 3000
    compacted = compact_tool_result("execute_cmd", {"status": "ok", "stdout": stdout, "stderr": stderr, "returncode": 0})

    assert compacted["status"] == "ok"
    assert compacted["returncode"] == 0
    assert compacted["stdout"]["omitted"] is True
    assert compacted["stderr"]["omitted"] is True


def test_compacted_tool_call_preserves_id_name_and_valid_json_arguments():
    content = "x" * 10000
    original = _tool_call("call-1", "write_content", {"path": "a.html", "content": content})

    compacted = compact_tool_call_for_history(original)
    args = json.loads(compacted["function"]["arguments"])

    assert compacted["id"] == "call-1"
    assert compacted["function"]["name"] == "write_content"
    assert args["path"] == "a.html"
    assert args["content"]["omitted"] is True
    assert json.loads(original["function"]["arguments"])["content"] == content


def test_controller_history_does_not_retain_full_content_but_execution_args_do():
    captured = []
    sent = []
    content = "0123456789" * 10000

    async def completion(**kwargs):
        captured.append(kwargs)
        if len(captured) == 1:
            return _message([_tool_call("call-write", "write_content", {"path": r"C:\tmp\big.html", "content": content})])
        return _message([_tool_call("call-answer", "answer_text", _answer_payload("Готово", ["step_1"]))])

    async def send_command(device_id, action, params):
        sent.append((action, dict(params)))
        return {
            "status": "ok",
            "path": params["path"],
            "bytes_written": len(params["content"].encode("utf-8")),
            "sha256": sha256_text(params["content"]),
        }

    result = asyncio.run(process_non_pipeline_command(
        user_message="create big file",
        device_id="device-1",
        device_info={"hostname": "devbox", "os": "Windows"},
        send_command_fn=send_command,
        get_file_link_fn=lambda device_id, path: "/download",
        chat_history=[],
        user_id=1,
        chat_id=1,
        modes={},
        poll_task_id=None,
        cfg={"model": "mock-model", "max_tokens": 512, "answer_auditor_enabled": False},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=3,
        pick_model_fn=lambda cfg, modes: "mock-model",
        chat_completion_request_fn=completion,
    ))

    assert sent == [("write_content", {"path": r"C:\tmp\big.html", "content": content})]
    assert result["answer"] == "Готово"
    second_messages = json.dumps(captured[1]["messages"], ensure_ascii=False)
    assert content not in second_messages
    assert sha256_text(content) in second_messages
    assistant_history = next(message for message in captured[1]["messages"] if message.get("tool_calls"))
    history_args = json.loads(assistant_history["tool_calls"][0]["function"]["arguments"])
    assert history_args["content"]["omitted"] is True


def test_answer_text_after_terminal_sufficient_still_works_with_compacted_evidence():
    huge_stdout = "created\n" + ("x" * 20000)
    entry = {
        "step_id": "step_1",
        "idx": 1,
        "action": "execute_cmd",
        "tool_name": "execute_cmd",
        "tool_type": "typed",
        "status": "success",
        "summary": "returncode=0",
        "result": {"returncode": 0, "stdout": huge_stdout, "stderr": "", "terminal_sufficient": True},
    }
    wrapped = wrap_tool_result_for_llm(entry)

    assert wrapped["result"]["stdout"]["omitted"] is True
    payload = _answer_payload("Готово", ["step_1"])
    assert validate_answer_text_payload(payload, [entry]) == payload


def test_answer_auditor_infra_fallback_accepts_grounded_answer_with_compacted_evidence():
    content = "large output" * 3000
    journal = [{
        "step_id": "step_1",
        "idx": 1,
        "action": "execute_cmd",
        "tool_name": "execute_cmd",
        "tool_type": "typed",
        "status": "success",
        "summary": "returncode=0",
        "result": compact_tool_result("execute_cmd", {"returncode": 0, "stdout": content, "stderr": ""}),
    }]
    payload = _answer_payload("Команда выполнена.", ["step_1"])

    assert journal[0]["result"]["stdout"]["omitted"] is True
    assert validate_answer_text_payload(payload, journal) == payload

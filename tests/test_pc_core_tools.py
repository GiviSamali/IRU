import asyncio
import base64
import inspect
import json

import pytest

from server.controller_tools import WORKER_TOOLS
from server.pc_core_tools import alias_key, run_pc_core_tool
from server.tool_completion import synthesize_terminal_answer_payload, tool_result_terminal_sufficient
from server.tool_contracts import get_tool_contract
from server.tool_registry import DEVICE_TOOL_SCHEMAS, canonical_tool_name, list_tools


def _stdout(payload: dict) -> dict:
    return {"status": "success", "returncode": 0, "stdout": json.dumps(payload, ensure_ascii=False), "stderr": ""}


def _decode_ps(params: dict) -> str:
    command = params["command"]
    marker = "-EncodedCommand "
    encoded = command.split(marker, 1)[1].strip().split()[0]
    return base64.b64decode(encoded).decode("utf-16le")


def test_resolve_path_aliases_include_downloads_ru_and_en():
    assert alias_key("downloads") == "downloads"
    assert alias_key("загрузки") == "downloads"


def test_fs_open_folder_later_window_success_beats_launch_noise():
    calls = []

    async def send(device_id, action, params):
        calls.append((action, dict(params)))
        if action == "execute_cmd" and len(calls) == 1:
            return _stdout({
                "status": "success",
                "resolved_path": r"C:\Users\russa\Downloads",
                "exists": True,
                "type": "dir",
            })
        if action == "execute_cmd":
            return {"status": "error", "stderr": "operation timed out after launch"}
        if action == "window.find":
            return {
                "status": "found",
                "window": {"title": "Downloads", "pid": 100, "process_name": "explorer.exe", "visible": True},
            }
        raise AssertionError(action)

    result = asyncio.run(run_pc_core_tool(
        "fs.open_folder",
        {"path_or_alias": "downloads"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert [action for action, _ in calls] == ["execute_cmd", "execute_cmd", "window.find"]
    assert result["status"] == "opened"
    assert result["window_found"] is True
    assert result["completion_state"] == "success"
    assert result["terminal_sufficient"] is True

    entry = {"tool_name": "fs.open_folder", "step_id": "step_2", "result": result}
    assert tool_result_terminal_sufficient(entry) is True
    payload = synthesize_terminal_answer_payload(entry)
    assert payload["answer_type"] == "grounded_report"
    assert payload["basis"] == ["step_2"]
    assert "Готово" in payload["text"]


def test_window_find_success_is_terminal_enough_for_final_answer():
    entry = {
        "tool_name": "window.find",
        "step_id": "step_3",
        "result": {
            "status": "found",
            "window": {"title": "Downloads", "visible": True},
        },
    }

    assert tool_result_terminal_sufficient(entry) is True
    payload = synthesize_terminal_answer_payload(entry)
    assert payload["answer_type"] == "grounded_report"
    assert payload["basis"] == ["step_3"]
    assert "окно найдено" in payload["text"].lower()


@pytest.mark.parametrize("tool_name", ["fs.resolve_path", "fs.stat", "fs.list_dir", "fs.read_file"])
def test_fs_inspection_tools_are_not_terminal_sufficient_by_default(tool_name):
    entry = {
        "tool_name": tool_name,
        "step_id": "step_1",
        "result": {"status": "success", "resolved_path": r"C:\tmp\a.txt"},
    }

    assert tool_result_terminal_sufficient(entry) is False


def test_fs_patch_file_success_is_terminal_sufficient():
    entry = {
        "tool_name": "fs.patch_file",
        "step_id": "step_1",
        "result": {"status": "patched", "path": r"C:\tmp\a.txt"},
    }

    assert tool_result_terminal_sufficient(entry) is True


def test_fs_open_folder_window_found_is_terminal_sufficient():
    entry = {
        "tool_name": "fs.open_folder",
        "step_id": "step_1",
        "result": {"status": "opened", "resolved_path": r"C:\Users\russa\Downloads", "window_found": True},
    }

    assert tool_result_terminal_sufficient(entry) is True


def test_fs_open_folder_downloads_alias_tries_localized_titles_after_timeout():
    calls = []

    async def send(device_id, action, params):
        calls.append((action, dict(params)))
        if action == "execute_cmd" and len(calls) == 1:
            return _stdout({
                "status": "success",
                "resolved_path": r"C:\Users\russa\Downloads",
                "exists": True,
                "type": "dir",
                "source": "alias",
                "alias_key": "downloads",
            })
        if action == "execute_cmd":
            return {"status": "error", "stderr": "operation timed out after launch"}
        if action == "window.find":
            title = params["title_contains"]
            if title == "Загрузки":
                return {
                    "status": "found",
                    "window": {"title": "Загрузки - Проводник", "pid": 100, "process_name": "explorer.exe", "visible": True},
                }
            return {"status": "not_found", "matches": []}
        raise AssertionError(action)

    result = asyncio.run(run_pc_core_tool(
        "fs_open_folder",
        {"path_or_alias": "downloads"},
        send_command_fn=send,
        device_id="device-1",
    ))

    window_titles = [params["title_contains"] for action, params in calls if action == "window.find"]
    assert window_titles[:2] == ["Downloads", "Загрузки"]
    assert result["status"] == "opened"
    assert result["window_found"] is True
    assert result["completion_state"] == "success"
    assert result["terminal_sufficient"] is True


def test_fs_list_dir_caps_limit_at_500():
    decoded_scripts = []

    async def send(device_id, action, params):
        if action != "execute_cmd":
            raise AssertionError(action)
        decoded_scripts.append(_decode_ps(params))
        if len(decoded_scripts) == 1:
            return _stdout({
                "status": "success",
                "resolved_path": r"C:\Users\russa\Downloads",
                "exists": True,
                "type": "dir",
            })
        return _stdout({
            "status": "success",
            "resolved_path": r"C:\Users\russa\Downloads",
            "returned_count": 500,
            "items": [],
            "truncated": True,
        })

    result = asyncio.run(run_pc_core_tool(
        "fs_list_dir",
        {"path_or_alias": "downloads", "limit": 5000},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["returned_count"] == 500
    assert "Select-Object -Skip 0 -First 500" in decoded_scripts[1]


def test_documents_alias_resolve_script_uses_known_folder_lookup():
    decoded_scripts = []

    async def send(device_id, action, params):
        decoded_scripts.append(_decode_ps(params))
        return _stdout({
            "status": "success",
            "resolved_path": r"C:\Users\russa\OneDrive\Документы",
            "exists": True,
            "type": "dir",
            "source": "alias",
            "alias_key": "documents",
        })

    result = asyncio.run(run_pc_core_tool(
        "fs_resolve_path",
        {"path_or_alias": "documents"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["alias_key"] == "documents"
    assert "GetFolderPath('MyDocuments')" in decoded_scripts[0]
    assert "User Shell Folders" in decoded_scripts[0]


def test_fs_read_file_caps_preview_and_returns_sha_evidence():
    decoded_scripts = []

    async def send(device_id, action, params):
        decoded_scripts.append(_decode_ps(params))
        if len(decoded_scripts) == 1:
            return _stdout({
                "status": "success",
                "resolved_path": r"C:\tmp\a.txt",
                "exists": True,
                "type": "file",
            })
        return _stdout({
            "status": "success",
            "path": r"C:\tmp\a.txt",
            "content": "hello",
            "truncated": True,
            "sha256": "a" * 64,
        })

    result = asyncio.run(run_pc_core_tool(
        "fs_read_file",
        {"path": r"C:\tmp\a.txt", "max_chars": 999999},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["sha256"] == "a" * 64
    assert result["truncated"] is True
    assert "Min(100000" in decoded_scripts[1]


def test_fs_write_file_uses_backup_on_replace_by_default():
    decoded_scripts = []

    async def send(device_id, action, params):
        decoded_scripts.append(_decode_ps(params))
        if len(decoded_scripts) == 1:
            return _stdout({"status": "success", "resolved_path": r"C:\tmp\a.txt", "exists": True, "type": "file"})
        return _stdout({
            "status": "written",
            "path": r"C:\tmp\a.txt",
            "backup_path": r"C:\tmp\a.txt.iru.bak.1",
            "sha256": "b" * 64,
            "terminal_sufficient": True,
            "completion_state": "success",
        })

    result = asyncio.run(run_pc_core_tool(
        "fs_write_file",
        {"path": r"C:\tmp\a.txt", "content": "new"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["backup_path"].endswith(".iru.bak.1")
    assert "Copy-Item -LiteralPath $path -Destination $backupPath -Force" in decoded_scripts[1]


def test_fs_patch_file_supports_structured_replace_operations():
    decoded_scripts = []

    async def send(device_id, action, params):
        decoded_scripts.append(_decode_ps(params))
        if len(decoded_scripts) == 1:
            return _stdout({"status": "success", "resolved_path": r"C:\tmp\a.txt", "exists": True, "type": "file"})
        return _stdout({
            "status": "patched",
            "path": r"C:\tmp\a.txt",
            "operations_applied": 1,
            "before_sha256": "a" * 64,
            "after_sha256": "b" * 64,
            "terminal_sufficient": True,
            "completion_state": "success",
        })

    result = asyncio.run(run_pc_core_tool(
        "fs_patch_file",
        {"path": r"C:\tmp\a.txt", "operations": [{"op": "replace", "find": "old", "replace": "new"}]},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["status"] == "patched"
    assert "marker_not_found" in decoded_scripts[1]
    assert "Get-FileHash -Algorithm SHA256" in decoded_scripts[1]


def test_fs_rename_rejects_separator_in_new_name_before_dispatch():
    async def send(device_id, action, params):
        raise AssertionError("invalid rename must not touch agent")

    result = asyncio.run(run_pc_core_tool(
        "fs_rename",
        {"path": r"C:\tmp\a.txt", "new_name": r"nested\b.txt"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result == {"status": "error", "error": "invalid_new_name"}


def test_fs_delete_requires_confirmation_for_permanent_delete():
    calls = []

    async def send(device_id, action, params):
        calls.append((action, dict(params)))
        return _stdout({"status": "success", "resolved_path": r"C:\tmp\a.txt", "exists": True, "type": "file"})

    result = asyncio.run(run_pc_core_tool(
        "fs_delete",
        {"path": r"C:\tmp\a.txt", "mode": "permanent"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["status"] == "needs_confirmation"
    assert result["reason"] == "destructive_action"
    assert [action for action, _ in calls] == ["execute_cmd"]


def test_fs_copy_to_risky_destination_requires_confirmation():
    calls = []

    async def send(device_id, action, params):
        calls.append((action, dict(params)))
        return _stdout({"status": "success", "resolved_path": r"C:\tmp\a.txt", "exists": True, "type": "file"})

    result = asyncio.run(run_pc_core_tool(
        "fs_copy",
        {"source": r"C:\tmp\a.txt", "destination": r"C:\Windows\a.txt"},
        send_command_fn=send,
        device_id="device-1",
    ))

    assert result["status"] == "needs_confirmation"
    assert result["reason"] == "risky_system_path"
    assert [action for action, _ in calls] == ["execute_cmd"]


def test_pc_core_tools_are_public_contracted_and_worker_available():
    public_names = {tool["name"] for tools in list_tools("all").values() for tool in tools}
    schema_names = {tool["function"]["name"] for tool in DEVICE_TOOL_SCHEMAS}
    worker_names = {tool["function"]["name"] for tool in WORKER_TOOLS}

    expected = {
        "fs.resolve_path": "fs_resolve_path",
        "fs.open_folder": "fs_open_folder",
        "fs.list_dir": "fs_list_dir",
        "fs.stat": "fs_stat",
        "fs.read_file": "fs_read_file",
        "fs.write_file": "fs_write_file",
        "fs.patch_file": "fs_patch_file",
        "fs.rename": "fs_rename",
        "fs.copy": "fs_copy",
        "fs.move": "fs_move",
        "fs.delete": "fs_delete",
        "app.open_file": "app_open_file",
    }
    for canonical, function_name in expected.items():
        assert canonical in public_names
        assert get_tool_contract(canonical)
        assert function_name in schema_names
        assert function_name in worker_names
        assert canonical_tool_name(function_name) == canonical


def test_pc_core_tools_do_not_use_dynamic_tool_loading():
    import server.pc_core_tools as pc_core_tools

    source = inspect.getsource(pc_core_tools)
    assert "importlib" not in source
    assert "load_proposal" not in source


def test_app_open_url_user_facing_answer_remains_success_for_opened_unverified():
    payload = synthesize_terminal_answer_payload({
        "tool_name": "app.open_url",
        "step_id": "step_1",
        "result": {
            "status": "opened_unverified",
            "url": "https://irumode.online/",
            "launched": True,
            "window_found": False,
        },
    })

    assert "Готово" in payload["text"]
    assert "открыл" in payload["text"]
    assert "недоступ" not in payload["text"].lower()

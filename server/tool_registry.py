from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from .device_context import build_minimal_llm_context
except ImportError:
    from device_context import build_minimal_llm_context


CANONICAL_TOOL_NAMES = {
    "system_list_tools": "system.list_tools",
    "tool_propose": "tool.propose",
    "tool_list_proposals": "tool.list_proposals",
    "tool_get_proposal": "tool.get_proposal",
    "tool_update_proposal_status": "tool.update_proposal_status",
    "fs_resolve_path": "fs.resolve_path",
    "fs_open_folder": "fs.open_folder",
    "fs_list_dir": "fs.list_dir",
    "fs_stat": "fs.stat",
    "fs_read_file": "fs.read_file",
    "fs_write_file": "fs.write_file",
    "fs_patch_file": "fs.patch_file",
    "fs_rename": "fs.rename",
    "fs_copy": "fs.copy",
    "fs_move": "fs.move",
    "fs_delete": "fs.delete",
    "memory_get_stats": "memory.get_stats",
    "memory_list_facts": "memory.list_facts",
    "device_get_passport": "device.get_passport",
    "device_refresh_state": "device.refresh_state",
    "device_activate": "device.activate",
    "device_repair_activation": "device.repair_activation",
    "device_check_runtime": "device.check_runtime",
    "device_prepare_runtime": "device.prepare_runtime",
    "device_repair_runtime": "device.repair_runtime",
    "window_list": "window.list",
    "window_find": "window.find",
    "window_verify": "window.verify",
    "window_focus": "window.focus",
    "window_close": "window.close",
    "app_launch": "app.launch",
    "app_open_url": "app.open_url",
    "app_open_file": "app.open_file",
    "app_verify_launch": "app.verify_launch",
    "app_close": "app.close",
    "system_get_last_run_summary": "system.get_last_run_summary",
    "answer_text": "answer.text",
    "answer_ask_clarification": "answer.ask_clarification",
    "answer_report_failure": "answer.report_failure",
    "answer_request_confirmation": "answer.request_confirmation",
    "write_content": "write_content",
    "execute_cmd": "execute_cmd",
}


TOOL_METADATA = {
    "system.list_tools": {
        "category": "system",
        "tool_type": "system",
        "tool_label": "Tool registry",
        "purpose": "List available typed tools grouped by category",
        "when_to_use": ["need available capabilities", "before choosing a tool"],
        "returns": "compact grouped tool registry",
        "danger": "safe",
    },
    "system.get_last_run_summary": {
        "category": "system",
        "tool_type": "system",
        "tool_label": "Last run summary",
        "purpose": "Explain the previous task/run result without retrying the action",
        "when_to_use": ["user asks what happened", "user asks why the previous run failed", "need last failed task evidence"],
        "returns": "last task status, used tools, failed tools, evidence and failure reason",
        "danger": "safe",
    },
    "tool.propose": {
        "category": "tooling",
        "tool_type": "proposal",
        "tool_label": "Tool proposal",
        "purpose": "Create a structured candidate ToolContract for future review without installing or executing a production tool",
        "when_to_use": [
            "user asks whether IRU can create a tool",
            "user asks to add a new future tool capability",
            "enough details exist to draft a safe tool candidate",
        ],
        "returns": "proposal_id, name, proposed status and summary",
        "danger": "write",
    },
    "tool.list_proposals": {
        "category": "tooling",
        "tool_type": "proposal",
        "tool_label": "Tool proposals",
        "purpose": "List current user's saved tool proposals",
        "when_to_use": ["user asks for their tool proposals", "need to inspect candidate tools"],
        "returns": "current user's tool proposals",
        "danger": "safe",
    },
    "tool.get_proposal": {
        "category": "tooling",
        "tool_type": "proposal",
        "tool_label": "Tool proposal detail",
        "purpose": "Read one current-user tool proposal by id",
        "when_to_use": ["user asks details about one proposal", "need proposal evidence before answering"],
        "returns": "one proposal if owned by current user",
        "danger": "safe",
    },
    "tool.update_proposal_status": {
        "category": "tooling",
        "tool_type": "proposal",
        "tool_label": "Tool proposal status",
        "purpose": "Update status or notes for a current-user proposal without changing production tools",
        "when_to_use": ["internal/admin review flow", "current user explicitly asks to change proposal status"],
        "returns": "updated proposal",
        "danger": "write",
        "visibility": "internal",
    },
    "fs.resolve_path": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Resolve path",
        "purpose": "Resolve human-friendly aliases and relative paths into absolute local paths",
        "when_to_use": ["need local path evidence", "before file/folder operations", "user mentions Downloads/Documents/Desktop by name"],
        "returns": "resolved path, exists flag and type",
        "danger": "safe",
    },
    "fs.open_folder": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Open folder",
        "purpose": "Open a folder in Explorer/file manager and verify visible folder window when possible",
        "when_to_use": ["user asks to open a folder", "user asks to show Downloads/Documents/Desktop"],
        "returns": "open status, resolved path and window evidence",
        "danger": "process_start",
    },
    "fs.list_dir": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "List folder",
        "purpose": "List folder contents with capped output",
        "when_to_use": ["user asks what files are in a folder", "need directory contents"],
        "returns": "capped directory item list",
        "danger": "safe",
    },
    "fs.stat": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "File stat",
        "purpose": "Check file/folder existence, type, size and modified time",
        "when_to_use": ["need file existence evidence", "need cheap file/folder metadata"],
        "returns": "existence/type/size/mtime and optional sha256",
        "danger": "safe",
    },
    "fs.read_file": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Read file",
        "purpose": "Read a text file with output caps and sha256 evidence",
        "when_to_use": ["user asks to read a file", "need file content preview"],
        "returns": "text content preview, truncation flag and sha256",
        "danger": "safe",
    },
    "fs.write_file": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Write file",
        "purpose": "Create, append, or replace a text file with backup on replace by default",
        "when_to_use": ["user asks to create a file", "user asks to write text to a file"],
        "returns": "write status, path, bytes and sha256",
        "danger": "write",
    },
    "fs.patch_file": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Patch file",
        "purpose": "Patch a text file through deterministic replace/insert/append/delete_block operations",
        "when_to_use": ["user asks to edit part of a text file", "need targeted file modifications"],
        "returns": "patch status, backup path and before/after hashes",
        "danger": "write",
    },
    "fs.rename": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Rename",
        "purpose": "Rename one file or folder by new name",
        "when_to_use": ["user asks to rename a file or folder"],
        "returns": "old and new path",
        "danger": "write",
    },
    "fs.copy": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Copy",
        "purpose": "Copy a file or folder",
        "when_to_use": ["user asks to copy a file or folder"],
        "returns": "source and destination",
        "danger": "write",
    },
    "fs.move": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Move",
        "purpose": "Move a file or folder with risky path guards",
        "when_to_use": ["user asks to move a file or folder"],
        "returns": "old and new path",
        "danger": "write",
    },
    "fs.delete": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Delete",
        "purpose": "Delete a file/folder only through guarded confirmation-aware behavior",
        "when_to_use": ["user asks to delete a file or folder"],
        "returns": "deleted or needs_confirmation status",
        "danger": "destructive",
    },
    "memory.get_stats": {
        "category": "memory",
        "tool_type": "typed",
        "tool_label": "Memory stats",
        "purpose": "Read count of saved user memory facts from server-side memory",
        "when_to_use": [
            "user asks how many facts are saved in memory",
            "need current user memory count",
            "memory question that does not require device state",
        ],
        "returns": "facts_count for the current authenticated user",
        "danger": "safe",
    },
    "memory.list_facts": {
        "category": "memory",
        "tool_type": "typed",
        "tool_label": "Memory facts",
        "purpose": "List saved user memory facts from server-side memory",
        "when_to_use": [
            "user asks what IRU remembers",
            "need current user memory facts",
            "memory question that does not require device passport, local files, or shell",
        ],
        "returns": "compact facts list for the current authenticated user",
        "danger": "safe",
    },
    "device.get_passport": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Device passport",
        "purpose": "Get compact device passport and context handles",
        "when_to_use": [
            "user asks about device passport",
            "need current activation/runtime/health status",
            "need context handles for device data",
        ],
        "returns": "compact passport + context handles",
        "danger": "safe",
    },
    "device.refresh_state": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Обновление паспорта устройства",
        "purpose": "Get a fresh live device state snapshot and update the passport",
        "when_to_use": [
            "user asks current device state",
            "need to refresh device passport",
            "before performance diagnostics",
        ],
        "returns": "compact health summary + state handle",
        "danger": "safe",
        "uses_shell_internally": True,
    },
    "device.activate": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Активация устройства",
        "purpose": "Run soft device activation and store a validated activation summary",
        "when_to_use": ["activation is required", "device needs activation before work"],
        "returns": "compact activation summary",
        "danger": "safe",
    },
    "device.repair_activation": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Repair activation",
        "purpose": "Repair degraded or failed activation without claiming to install Python",
        "when_to_use": ["activation_status is degraded", "activation_status is activation_failed"],
        "returns": "compact activation summary",
        "danger": "safe",
    },
    "device.check_runtime": {
        "category": "python",
        "tool_type": "typed",
        "tool_label": "Проверка Python runtime",
        "purpose": "Check the managed Python runtime without creating or installing it",
        "when_to_use": ["need Python runtime facts", "before Python/PyQt work", "passport says runtime status is unknown"],
        "returns": "compact runtime summary + ctx://device/{device_id}/python",
        "danger": "safe",
    },
    "device.prepare_runtime": {
        "category": "python",
        "tool_type": "typed",
        "tool_label": "Подготовка Python runtime",
        "purpose": "Prepare an IRU-owned venv for stable Python execution",
        "when_to_use": [
            "user asks to prepare Python",
            "Python/PyQt task requires stable runtime",
            "passport says runtime_status missing/install_required/broken",
            "before creating or running Python apps",
        ],
        "returns": "compact runtime summary + ctx://device/{device_id}/python",
        "danger": "write/runtime",
    },
    "device.repair_runtime": {
        "category": "python",
        "tool_type": "typed",
        "tool_label": "Repair Python runtime",
        "purpose": "Repair or recreate a broken managed Python venv",
        "when_to_use": ["runtime_status is broken", "runtime_status is degraded", "managed venv is unusable"],
        "returns": "compact runtime summary + ctx://device/{device_id}/python",
        "danger": "write/runtime",
    },
    "window.list": {
        "category": "window",
        "tool_type": "typed",
        "tool_label": "Список окон",
        "purpose": "List top-level OS windows with pid, title, class, visibility, bounds, and process name",
        "when_to_use": ["need to inspect open windows", "user asks what windows are open"],
        "returns": "compact window list",
        "danger": "safe",
    },
    "window.find": {
        "category": "window",
        "tool_type": "typed",
        "tool_label": "Поиск окна",
        "purpose": "Find a top-level OS window by pid, title, class, process, and visibility",
        "when_to_use": ["user asks whether an app window is open", "need to locate a window before focus or close"],
        "returns": "best matching window + compact matches",
        "danger": "safe",
    },
    "window.verify": {
        "category": "window",
        "tool_type": "typed",
        "tool_label": "Проверка окна",
        "purpose": "Verify that a matching window exists and is visible without waiting for the GUI process to exit",
        "when_to_use": ["verify GUI launch", "check whether an existing app window is visible"],
        "returns": "verified flag + process/window status",
        "danger": "safe",
    },
    "window.focus": {
        "category": "window",
        "tool_type": "typed",
        "tool_label": "Фокус окна",
        "purpose": "Focus or restore one matching window",
        "when_to_use": ["user asks to bring an app window forward"],
        "returns": "focus status + window",
        "danger": "window_focus",
    },
    "window.close": {
        "category": "window",
        "tool_type": "typed",
        "tool_label": "Закрытие окна",
        "purpose": "Close one exactly matched window; refuses broad ambiguous matches",
        "when_to_use": ["user asks to close a specific window"],
        "returns": "close status + window/pid",
        "danger": "process_control",
    },
    "app.launch": {
        "category": "app",
        "tool_type": "typed",
        "tool_label": "Запуск приложения",
        "purpose": "Launch an application as a background process and attempt window verification",
        "when_to_use": ["launch GUI app", "open file or app and verify it appeared"],
        "returns": "pid + launch status + optional window",
        "danger": "process_start",
    },
    "app.open_url": {
        "category": "app",
        "tool_type": "typed",
        "tool_label": "Open URL",
        "purpose": "Open a URL in the default or selected browser and verify a visible browser window",
        "when_to_use": ["open URL", "open remembered link", "open webpage in browser"],
        "returns": "URL open status + browser/window evidence",
        "danger": "process_start",
    },
    "app.open_file": {
        "category": "app",
        "tool_type": "typed",
        "tool_label": "Open file",
        "purpose": "Open a local file with the default app and verify a visible window when possible",
        "when_to_use": ["user asks to open a file", "need default application launch for a local file"],
        "returns": "open status, path and optional process/window evidence",
        "danger": "process_start",
    },
    "app.verify_launch": {
        "category": "app",
        "tool_type": "typed",
        "tool_label": "Проверка запуска приложения",
        "purpose": "Verify an app launch through the universal window layer",
        "when_to_use": ["confirm launched GUI app is visible", "verify a pid has a window"],
        "returns": "verified flag + window/process status",
        "danger": "safe",
    },
    "app.close": {
        "category": "app",
        "tool_type": "typed",
        "tool_label": "Закрытие приложения",
        "purpose": "Close an application by pid through window.close",
        "when_to_use": ["user asks to close a launched app"],
        "returns": "close status",
        "danger": "process_control",
    },
    "write_content": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Запись файла",
        "purpose": "Create or overwrite a text file without shell escaping",
        "when_to_use": ["create txt/json/py/html file", "write text to a file"],
        "returns": "file write result",
        "danger": "write",
    },
    "execute_cmd": {
        "category": "fallback",
        "tool_type": "fallback",
        "tool_label": "PowerShell / shell fallback",
        "purpose": "Low-level shell fallback, use only when typed tools are unavailable",
        "when_to_use": ["no typed tool exists", "no playbook exists"],
        "returns": "command result",
        "danger": "depends_on_command",
    },
    "get_file_link": {
        "category": "artifact",
        "tool_type": "typed",
        "tool_label": "Download link",
        "purpose": "Create a temporary server download link for an exact file path produced or identified by the run",
        "when_to_use": ["user asks to download a created file", "need a UI-safe link for an exact known file"],
        "returns": "download URL and file path",
        "danger": "safe",
        "visibility": "internal",
        "status": "hidden",
    },
    "web_search": {
        "category": "web",
        "tool_type": "typed",
        "tool_label": "Web search",
        "purpose": "Search the web through the configured Tavily integration when fresh external facts are required",
        "when_to_use": ["fresh current information is required", "news or current documentation is required"],
        "returns": "compact web search results",
        "danger": "network",
    },
    "remember_fact": {
        "category": "memory",
        "tool_type": "typed",
        "tool_label": "Remember fact",
        "purpose": "Store a validated user or device memory fact",
        "when_to_use": ["a stable user preference or device fact should be remembered"],
        "returns": "memory write status and fact id",
        "danger": "write",
        "visibility": "internal",
        "status": "hidden",
    },
    "forget_fact": {
        "category": "memory",
        "tool_type": "typed",
        "tool_label": "Forget fact",
        "purpose": "Delete an existing memory fact by id",
        "when_to_use": ["user asks IRU to forget a remembered fact"],
        "returns": "memory delete status",
        "danger": "write",
        "visibility": "internal",
        "status": "hidden",
    },
    "answer.text": {
        "category": "answer",
        "tool_type": "answer",
        "tool_label": "Ответ",
        "purpose": "Terminal user-facing text response.",
        "when_to_use": ["final user-facing answer", "conceptual answer", "grounded report after tool result"],
        "returns": "terminal answer payload",
        "danger": "safe",
    },
    "answer.ask_clarification": {
        "category": "answer",
        "tool_type": "answer",
        "tool_label": "Уточняющий вопрос",
        "purpose": "Terminal clarification question when the task cannot proceed safely or meaningfully.",
        "when_to_use": ["need user input", "task is ambiguous or unsafe without clarification"],
        "returns": "terminal clarification payload",
        "danger": "safe",
    },
    "answer.report_failure": {
        "category": "answer",
        "tool_type": "answer",
        "tool_label": "Сообщение об ошибке",
        "purpose": "Terminal failure report.",
        "when_to_use": ["unrecoverable tool error", "policy or configuration prevents completion"],
        "returns": "terminal failure payload",
        "danger": "safe",
    },
    "answer.request_confirmation": {
        "category": "answer",
        "tool_type": "answer",
        "tool_label": "Запрос подтверждения",
        "purpose": "Terminal confirmation request before a risky action.",
        "when_to_use": ["dangerous or destructive action requires confirmation"],
        "returns": "terminal confirmation payload",
        "danger": "confirmation",
    },
}


PC_CORE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "fs_resolve_path",
            "description": "Resolve aliases like downloads/загрузки/documents/desktop/home into absolute local paths. Use before filesystem operations when path is ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_alias": {"type": "string"},
                    "base_path": {"type": "string"},
                    "must_exist": {"type": "boolean", "default": False},
                    "expected_type": {"type": "string", "enum": ["any", "file", "dir"], "default": "any"},
                },
                "required": ["path_or_alias"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_open_folder",
            "description": "Open a folder in Explorer/file manager and return normalized window evidence. Prefer over execute_cmd for opening folders like Downloads.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_alias": {"type": "string"},
                    "focus": {"type": "boolean", "default": True},
                },
                "required": ["path_or_alias"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_list_dir",
            "description": "List folder contents safely with a limit. Prefer over execute_cmd dir/ls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_alias": {"type": "string"},
                    "limit": {"type": "integer", "default": 100},
                    "offset": {"type": "integer", "default": 0},
                    "include_hidden": {"type": "boolean", "default": False},
                    "filter": {"type": "string"},
                },
                "required": ["path_or_alias"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_stat",
            "description": "Check whether a file/folder exists and return type, size, timestamps, and cheap sha256 for small files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_alias": {"type": "string"},
                    "expected_type": {"type": "string", "enum": ["any", "file", "dir"], "default": "any"},
                },
                "required": ["path_or_alias"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_read_file",
            "description": "Read a text file safely with max_chars cap and sha256. Rejects binary previews.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 20000},
                    "encoding": {"type": "string", "default": "auto"},
                    "offset": {"type": "integer", "default": 0},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_write_file",
            "description": "Create, append, or replace a text file with backup on replace by default. Prefer over raw shell for file writes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {"type": "string", "enum": ["create_only", "create_or_replace", "append"], "default": "create_or_replace"},
                    "backup": {"type": "boolean", "default": True},
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_patch_file",
            "description": "Patch a text file using replace/insert_before/insert_after/append/delete_block operations with backup by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "operations": {"type": "array", "items": {"type": "object"}},
                    "backup": {"type": "boolean", "default": True},
                },
                "required": ["path", "operations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_rename",
            "description": "Rename a file or folder. new_name must be a simple name, not a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "new_name": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "new_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_copy",
            "description": "Copy a file or folder with overwrite control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_move",
            "description": "Move a file or folder with risky path guards.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_delete",
            "description": "Delete a file/folder safely. Permanent delete requires confirmed=true; folder delete requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "mode": {"type": "string", "enum": ["trash", "permanent"], "default": "trash"},
                    "confirmed": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_open_file",
            "description": "Open a file with the default app and verify a visible window when possible. Prefer over execute_cmd for opening local files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "app_hint": {"type": "string", "default": "default"},
                    "focus": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
        },
    },
]


DEVICE_TOOL_SCHEMAS = [
    *PC_CORE_TOOL_SCHEMAS,
    {
        "type": "function",
        "function": {
            "name": "system_list_tools",
            "description": "List compact available tools grouped by category. Use before choosing a capability when unsure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["all", "system", "tooling", "memory", "device", "files", "python", "window", "app", "artifact", "web", "answer"],
                        "default": "all",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_get_last_run_summary",
            "description": "Get a compact summary of the previous task/run for this chat/user. Use when the user asks what happened or why the previous task failed. Do not retry the previous action unless explicitly asked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer", "description": "Optional current chat id."},
                    "include_success": {"type": "boolean", "default": True},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_propose",
            "description": "Create a structured proposal for a future tool. This does not install, execute, import, or modify production tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Canonical candidate name like office.create_docx."},
                    "title": {"type": "string"},
                    "problem": {"type": "string"},
                    "purpose": {"type": "string"},
                    "category": {"type": "string"},
                    "risk_level": {"type": "string", "enum": ["safe", "read_only", "write", "runtime", "process_start", "process_control", "network", "destructive", "confirmation_required", "fallback"]},
                    "permissions": {"type": "array", "items": {"type": "string"}},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "evidence_contract": {"type": "object"},
                    "side_effects": {"type": "array", "items": {"type": "string"}},
                    "idempotency": {"type": "string"},
                    "cleanup": {"type": "string"},
                    "rollback": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "object"}},
                    "test_plan": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                    "notes": {"type": "string"},
                },
                "required": ["name", "problem", "purpose"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_list_proposals",
            "description": "List the current user's tool proposals. Does not expose proposals from other users.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["proposed", "reviewing", "approved", "rejected", "implemented", "deprecated"]},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_get_proposal",
            "description": "Get one current-user tool proposal by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "integer"},
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_update_proposal_status",
            "description": "Update status/notes for a current-user tool proposal. Does not mark production implementation by itself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["proposed", "reviewing", "approved", "rejected", "implemented", "deprecated"]},
                    "notes": {"type": "string"},
                },
                "required": ["proposal_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get_stats",
            "description": "Get count of saved memory facts for the current authenticated user. This is server-side and works without a connected device. Use for memory count questions instead of device_get_passport or execute_cmd.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_open_url",
            "description": "Open a URL in the default or selected browser and verify a visible browser window. Prefer this over execute_cmd for URL opening.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "url": {"type": "string"},
                    "browser": {"type": "string", "enum": ["default", "edge", "comet", "chrome", "auto"], "default": "default"},
                    "focus": {"type": "boolean", "default": True},
                    "timeout_sec": {"type": "number", "default": 7},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list_facts",
            "description": "List saved memory facts for the current authenticated user. This is server-side and works without a connected device. Use for questions about what IRU remembers instead of device_get_passport, local files, or execute_cmd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum facts to return. Defaults to 20, maximum 100.",
                        "default": 20,
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_get_passport",
            "description": "Get compact device passport: hostname, online, activation/runtime/health/identity status, capabilities, and context handles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_refresh_state",
            "description": "Get a fresh live state snapshot for a device and update its passport. Do not use raw shell for device-state requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_activate",
            "description": "Run soft device activation and return compact activation summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "mode": {"type": "string", "enum": ["soft"], "default": "soft"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_repair_activation",
            "description": "Repair degraded or failed activation. This does not install managed Python.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_check_runtime",
            "description": "Check managed Python runtime status for a device without creating or installing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "packages": {"type": "array", "items": {"type": "string"}, "description": "Optional packages to check only."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_prepare_runtime",
            "description": "Prepare an IRU-managed Python venv if possible. Does not download Python or install requested packages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "packages": {"type": "array", "items": {"type": "string"}, "description": "Optional packages to check only after preparation."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_repair_runtime",
            "description": "Repair or recreate a broken managed Python venv. Does not download Python or install requested packages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "packages": {"type": "array", "items": {"type": "string"}, "description": "Optional packages to check only after repair."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_list",
            "description": "List top-level OS windows with title, pid, class, process, visibility, minimized state, and bounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "include_invisible": {"type": "boolean", "default": False},
                    "include_minimized": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_find",
            "description": "Find a top-level OS window by pid, title, regex, class, process, and visibility.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "pid": {"type": "integer"},
                    "title_contains": {"type": "string"},
                    "title_regex": {"type": "string"},
                    "class_name": {"type": "string"},
                    "process_name": {"type": "string"},
                    "visible": {"type": "boolean", "default": True},
                    "timeout_sec": {"type": "number", "default": 5},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_verify",
            "description": "Verify that a matching window exists and is visible. For GUI success use this instead of waiting for process exit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "pid": {"type": "integer"},
                    "title_contains": {"type": "string"},
                    "title_regex": {"type": "string"},
                    "class_name": {"type": "string"},
                    "process_name": {"type": "string"},
                    "require_visible": {"type": "boolean", "default": True},
                    "require_not_minimized": {"type": "boolean", "default": False},
                    "timeout_sec": {"type": "number", "default": 5},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_focus",
            "description": "Restore and focus one matching window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "handle": {"type": "integer"},
                    "pid": {"type": "integer"},
                    "title_contains": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_close",
            "description": "Close one exactly matched window. Refuses broad ambiguous matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "handle": {"type": "integer"},
                    "pid": {"type": "integer"},
                    "title_contains": {"type": "string"},
                    "title_regex": {"type": "string"},
                    "class_name": {"type": "string"},
                    "process_name": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_launch",
            "description": "Launch an app in the background and attempt window verification. Do not wait for GUI process exit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "expected_title": {"type": "string"},
                    "expected_process": {"type": "string"},
                    "timeout_sec": {"type": "number", "default": 5},
                    "env": {"type": "object"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_verify_launch",
            "description": "Verify a launched app by pid and optional expected title/process through window.verify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "pid": {"type": "integer"},
                    "expected_title": {"type": "string"},
                    "expected_process": {"type": "string"},
                    "timeout_sec": {"type": "number", "default": 5},
                },
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_close",
            "description": "Close an app by pid through window.close.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "pid": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_text",
            "description": "Terminal user-facing answer. Use for conceptual text or grounded reports after current-run tool results. Server-side only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer_type": {
                        "type": "string",
                        "enum": ["pure_text", "grounded_report", "partial_report", "error_report", "clarification", "failure"],
                    },
                    "text": {"type": "string"},
                    "basis": {"type": "array", "items": {"type": "string"}},
                    "self_check": {
                        "type": "object",
                        "properties": {
                            "depends_on_current_external_state": {"type": "boolean"},
                            "claims_completed_action": {"type": "boolean"},
                            "has_sufficient_evidence": {"type": "boolean"},
                            "missing_evidence_question": {"type": "string"},
                        },
                        "required": [
                            "depends_on_current_external_state",
                            "claims_completed_action",
                            "has_sufficient_evidence",
                            "missing_evidence_question",
                        ],
                    },
                },
                "required": ["answer_type", "text", "basis", "self_check"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_ask_clarification",
            "description": "Terminal clarification question. Server-side only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_report_failure",
            "description": "Terminal failure report. Server-side only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "reason": {"type": "string"},
                    "recoverable": {"type": "boolean"},
                    "suggested_next_action": {"type": "string"},
                    "basis": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["message", "reason", "recoverable", "suggested_next_action", "basis"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_request_confirmation",
            "description": "Terminal confirmation request before risky action. Server-side only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "action": {"type": "string"},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "command_preview": {"type": "string"},
                    "basis": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["message", "action", "risk", "command_preview", "basis"],
            },
        },
    },
]


def canonical_tool_name(name: str) -> str:
    return CANONICAL_TOOL_NAMES.get(name, name)


def _compact_tool_meta(name: str, meta: dict[str, Any]) -> dict[str, Any]:
    result = {
        "name": name,
        "purpose": meta.get("purpose"),
        "when_to_use": meta.get("when_to_use") or [],
        "danger": meta.get("danger"),
        "contract_version": "v1",
    }
    try:
        from .tool_contracts import get_tool_contract
    except ImportError:
        try:
            from tool_contracts import get_tool_contract  # type: ignore
        except ImportError:
            get_tool_contract = None  # type: ignore
    if get_tool_contract:
        contract = get_tool_contract(name) or {}
        if contract.get("risk_level"):
            result["risk_level"] = contract["risk_level"]
    if meta.get("returns"):
        result["returns"] = meta["returns"]
    if meta.get("uses_shell_internally") is not None:
        result["uses_shell_internally"] = bool(meta.get("uses_shell_internally"))
    return result


def list_tools(category: str = "all") -> dict[str, list[dict[str, Any]]]:
    requested = (category or "all").strip().lower()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, meta in TOOL_METADATA.items():
        group = meta.get("category") or "other"
        if meta.get("visibility", "public") != "public" or meta.get("status") == "hidden":
            continue
        if requested != "all" and group != requested:
            continue
        grouped.setdefault(group, []).append(_compact_tool_meta(name, meta))
    return grouped


def _result_status(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("error"):
            return "failed"
        if result.get("status") in {"failed", "error"}:
            return "failed"
        if result.get("status") == "skipped":
            return "skipped"
    return "success"


def compact_tool_summary(action: str, result: Any = None, command: str = "") -> str:
    name = canonical_tool_name(action)
    if isinstance(result, dict):
        if result.get("summary"):
            return str(result["summary"])[:240]
        if result.get("error"):
            return str(result["error"])[:240]
        if name == "device.refresh_state":
            health = result.get("health_summary") or {}
            status = result.get("status") or "ok"
            health_status = health.get("health_status") or "unknown"
            return f"status={status}; health={health_status}"
        if name in {"device.activate", "device.repair_activation"}:
            summary = result.get("activation_summary") or result.get("summary") or {}
            if isinstance(summary, dict):
                return f"activation={summary.get('activation_status') or summary.get('status') or 'unknown'}"
        if name in {"device.check_runtime", "device.prepare_runtime", "device.repair_runtime"}:
            summary = result.get("runtime_summary") or result.get("summary") or {}
            if isinstance(summary, dict):
                return f"runtime={summary.get('runtime_status') or result.get('status') or 'unknown'}"
            return f"runtime={result.get('status') or 'unknown'}"
        if name.startswith("window."):
            window = result.get("window") or result.get("match") or {}
            title = window.get("title") or result.get("window_title") or ""
            status = result.get("status") or "unknown"
            pid = window.get("pid") or result.get("pid") or ""
            bits = [f"status={status}"]
            if pid:
                bits.append(f"pid={pid}")
            if title:
                bits.append(f"title={title[:80]}")
            return "; ".join(bits)
        if name.startswith("app."):
            window = result.get("window") or {}
            status = result.get("status") or "unknown"
            pid = result.get("pid") or window.get("pid") or ""
            title = window.get("title") or result.get("window_title") or ""
            bits = [f"status={status}"]
            if name == "app.open_url":
                if result.get("url"):
                    bits.append(f"url={str(result.get('url'))[:120]}")
                if result.get("window_found") is not None:
                    bits.append(f"window_found={bool(result.get('window_found'))}")
                if result.get("focus_status"):
                    bits.append(f"focus={result.get('focus_status')}")
            if name == "app.open_file":
                if result.get("path"):
                    bits.append(f"path={str(result.get('path'))[:120]}")
                if result.get("window_found") is not None:
                    bits.append(f"window_found={bool(result.get('window_found'))}")
            if pid:
                bits.append(f"pid={pid}")
            if result.get("verified") is not None:
                bits.append(f"verified={bool(result.get('verified'))}")
            if title:
                bits.append(f"title={title[:80]}")
            return "; ".join(bits)
        if name == "device.get_passport":
            return f"passport={result.get('device_id') or 'device'}"
        if name.startswith("fs."):
            status = result.get("status") or "unknown"
            path = result.get("resolved_path") or result.get("path") or result.get("new_path") or result.get("destination") or ""
            if name == "fs.list_dir":
                return f"status={status}; items={result.get('returned_count', 0)}; path={path}"
            if name == "fs.open_folder":
                return f"status={status}; window_found={bool(result.get('window_found'))}; path={path}"
            if name in {"fs.read_file", "fs.write_file"} and result.get("sha256"):
                return f"status={status}; sha256={str(result.get('sha256'))[:12]}; path={path}"
            if name == "fs.patch_file" and result.get("after_sha256"):
                return f"status={status}; after_sha256={str(result.get('after_sha256'))[:12]}; path={path}"
            return f"status={status}; path={path}"
        if name.startswith("tool."):
            proposal_id = result.get("proposal_id") or (result.get("proposal") or {}).get("id")
            status = result.get("proposal_status") or result.get("status") or (result.get("proposal") or {}).get("status")
            count = result.get("count")
            if count is not None:
                return f"tool proposals={count}"
            bits = [f"status={status or 'unknown'}"]
            if proposal_id:
                bits.append(f"proposal_id={proposal_id}")
            if result.get("name"):
                bits.append(f"name={result.get('name')}")
            return "; ".join(bits)
        if name.startswith("memory."):
            count = result.get("facts_count")
            if count is not None:
                return f"facts={count}"
            return f"memory={result.get('status') or 'unknown'}"
        if name == "write_content":
            path = result.get("path") or result.get("file_path") or ""
            return f"file written: {path}" if path else "file write completed"
        if name == "execute_cmd":
            rc = result.get("returncode")
            return f"returncode={rc}" if rc is not None else "shell command completed"
        if name == "answer.text":
            return f"answer_type={result.get('answer_type') or 'unknown'}"
        if name == "answer.ask_clarification":
            return "clarification requested"
        if name == "answer.report_failure":
            return "failure reported"
        if name == "answer.request_confirmation":
            return "confirmation requested"
    if command:
        return command[:240]
    return f"{name} completed"


def tool_log_fields(action: str, result: Any = None, command: str = "", target_device_id: str | None = None) -> dict[str, Any]:
    name = canonical_tool_name(action)
    meta = TOOL_METADATA.get(name)
    if not meta:
        return {}
    return {
        "tool_name": name,
        "tool_type": meta.get("tool_type", "typed"),
        "tool_label": meta.get("tool_label") or name,
        "tool_status": _result_status(result),
        "target_device_id": target_device_id,
        "summary": compact_tool_summary(action, result, command),
    }


def tool_log_entry(
    action: str,
    result: Any = None,
    *,
    command: str = "",
    target_device_id: str | None = None,
    hostname: str | None = None,
    iteration: int | None = None,
) -> dict[str, Any]:
    entry = {
        "action": action,
        "command": command or f"[tool] {canonical_tool_name(action)}",
        "device_id": target_device_id,
        "target_device_id": target_device_id,
        "hostname": hostname or target_device_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    if iteration is not None:
        entry["iteration"] = iteration
    entry.update(tool_log_fields(action, result, command, target_device_id))
    return entry


def compact_device_passport(device_id: str, dev: dict | None, profile: dict | None = None) -> dict[str, Any]:
    context = build_minimal_llm_context(device_id, {device_id: dev or {}}, profile)
    current = context.get("current_device") or {}
    state = current.get("state_summary") if isinstance(current.get("state_summary"), dict) else {}
    return {
        "device_id": current.get("device_id") or device_id,
        "hostname": current.get("hostname") or device_id,
        "online": bool(current.get("online")),
        "activation_status": current.get("activation_status"),
        "runtime_status": current.get("runtime_status"),
        "python_runtime_status": current.get("python_runtime_status"),
        "python_version": current.get("python_version"),
        "pip_status": current.get("pip_status"),
        "health_status": current.get("health_status"),
        "last_snapshot_at": state.get("last_snapshot_at"),
        "identity_status": state.get("identity_status"),
        "capabilities_summary": current.get("capabilities_summary") or [],
        "context_handles": current.get("context_handles") or {},
    }

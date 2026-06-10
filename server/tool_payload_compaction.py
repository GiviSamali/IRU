from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


ARG_STRING_THRESHOLD = 4000
COMMAND_STRING_THRESHOLD = 3000
OPERATIONS_JSON_THRESHOLD = 6000
RESULT_STRING_THRESHOLD = 8000
STDIO_STRING_THRESHOLD = 4000
READ_FILE_CONTENT_THRESHOLD = 12000

LARGE_PAYLOAD_KEYS = {
    "body",
    "content",
    "command",
    "data",
    "document_content",
    "file_content",
    "html",
    "markdown",
    "output",
    "raw",
    "replace",
    "script",
    "stderr",
    "stdout",
    "text",
}

MARKER_KEYS = {"find", "start_marker", "end_marker"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except Exception:
        return len(str(value))


def _json_sha(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        payload = str(value)
    return sha256_text(payload)


def _preview_json(value: Any, preview_chars: int) -> Any:
    if isinstance(value, list):
        return [compact_value(item, max_chars=preview_chars, preview_chars=max(120, preview_chars // 3)) for item in value[:3]]
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 8:
                break
            preview[key] = compact_value(item, max_chars=preview_chars, preview_chars=max(120, preview_chars // 3))
        return preview
    return str(value)[:preview_chars]


def compact_value(value: Any, max_chars: int = 1200, preview_chars: int = 600) -> Any:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return {
            "omitted": True,
            "kind": "large_string",
            "chars": len(value),
            "sha256": sha256_text(value),
            "preview": value[:preview_chars],
            "tail_preview": value[-preview_chars:] if preview_chars else "",
        }
    if isinstance(value, list):
        json_chars = _json_chars(value)
        if json_chars > max_chars:
            return {
                "omitted": True,
                "kind": "large_json",
                "items": len(value),
                "json_chars": json_chars,
                "sha256": _json_sha(value),
                "preview": _preview_json(value, preview_chars),
            }
        return [compact_value(item, max_chars=max_chars, preview_chars=preview_chars) for item in value]
    if isinstance(value, dict):
        json_chars = _json_chars(value)
        if json_chars > max_chars:
            return {
                "omitted": True,
                "kind": "large_json",
                "items": len(value),
                "json_chars": json_chars,
                "sha256": _json_sha(value),
                "preview": _preview_json(value, preview_chars),
            }
        return {key: compact_value(item, max_chars=max_chars, preview_chars=preview_chars) for key, item in value.items()}
    return value


def _threshold_for_arg(tool_name: str, key: str) -> int:
    key_l = key.lower()
    if key_l == "command" or tool_name in {"execute_cmd", "app_launch", "app.launch"} and key_l == "command":
        return COMMAND_STRING_THRESHOLD
    if key_l in MARKER_KEYS:
        return ARG_STRING_THRESHOLD
    return ARG_STRING_THRESHOLD


def _compact_mapping_fields(tool_name: str, payload: dict[str, Any], *, result: bool = False) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in payload.items():
        key_l = key.lower()
        if key_l == "operations":
            if _json_chars(value) > OPERATIONS_JSON_THRESHOLD:
                compacted[key] = compact_value(value, max_chars=OPERATIONS_JSON_THRESHOLD, preview_chars=600)
            else:
                compacted[key] = compact_value(value, max_chars=ARG_STRING_THRESHOLD, preview_chars=600)
            continue
        if result and key_l in {"stdout", "stderr"}:
            compacted[key] = compact_value(value, max_chars=STDIO_STRING_THRESHOLD, preview_chars=600)
            continue
        if result and tool_name in {"fs.read_file", "fs_read_file"} and key_l == "content":
            compacted[key] = compact_value(value, max_chars=READ_FILE_CONTENT_THRESHOLD, preview_chars=1200)
            continue
        if key_l in LARGE_PAYLOAD_KEYS or key_l in MARKER_KEYS:
            threshold = RESULT_STRING_THRESHOLD if result else _threshold_for_arg(tool_name, key_l)
            compacted[key] = compact_value(value, max_chars=threshold, preview_chars=600)
            continue
        if isinstance(value, (dict, list)):
            compacted[key] = compact_value(value, max_chars=RESULT_STRING_THRESHOLD if result else ARG_STRING_THRESHOLD, preview_chars=600)
            continue
        compacted[key] = value
    return compacted


def compact_tool_args(tool_name: str, args: Any) -> Any:
    if not isinstance(args, dict):
        return compact_value(args, max_chars=ARG_STRING_THRESHOLD, preview_chars=600)
    return _compact_mapping_fields(tool_name, args, result=False)


def compact_tool_result(tool_name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return compact_value(result, max_chars=RESULT_STRING_THRESHOLD, preview_chars=600)
    return _compact_mapping_fields(tool_name, result, result=True)


def compact_tool_call_for_history(tool_call: dict[str, Any]) -> dict[str, Any]:
    compacted = copy.deepcopy(tool_call)
    function = compacted.setdefault("function", {})
    tool_name = str(function.get("name") or "")
    raw_args = function.get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except Exception:
        args = {"raw_arguments": raw_args}
    function["arguments"] = json.dumps(compact_tool_args(tool_name, args), ensure_ascii=False)
    return compacted


def compact_journal_entry_for_llm(entry: dict[str, Any]) -> dict[str, Any]:
    compacted = copy.deepcopy(entry)
    tool_name = str(compacted.get("tool_name") or compacted.get("action") or "")
    if "result" in compacted:
        compacted["result"] = compact_tool_result(tool_name, compacted.get("result"))
    command = compacted.get("command")
    if isinstance(command, str) and len(command) > COMMAND_STRING_THRESHOLD:
        meta = compact_value(command, max_chars=COMMAND_STRING_THRESHOLD, preview_chars=600)
        compacted["command"] = "[omitted large command]"
        compacted["command_omitted"] = True
        if isinstance(meta, dict):
            compacted["command_chars"] = meta.get("chars")
            compacted["command_sha256"] = meta.get("sha256")
            compacted["command_preview"] = meta.get("preview")
    return compacted

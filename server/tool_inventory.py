from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

try:
    from .tool_contracts import get_tool_contract
    from .tool_registry import DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name, list_tools
except ImportError:
    from tool_contracts import get_tool_contract  # type: ignore
    from tool_registry import DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name, list_tools  # type: ignore


VISIBILITY_VALUES = {"public", "internal", "hidden", "legacy"}

LEGACY_OR_INTERNAL_ACTIONS: dict[str, dict[str, Any]] = {
    "create_plan": {
        "visibility": "legacy",
        "executable": False,
        "notes": "legacy plan-tracking schema; not exposed in current non-pipeline/worker toolsets",
    },
    "mark_step": {
        "visibility": "legacy",
        "executable": False,
        "notes": "legacy plan-tracking schema; not exposed in current non-pipeline/worker toolsets",
    },
    "get_file_content": {
        "visibility": "internal",
        "executable": True,
        "notes": "agent-side file read action used by download endpoint, not an LLM public tool",
    },
    "list_dir": {
        "visibility": "internal",
        "executable": True,
        "notes": "agent-side file listing action, not exposed through current LLM tool schemas",
    },
    "device.get_cached_passport": {
        "visibility": "internal",
        "executable": True,
        "notes": "agent reconnect/cache action, not a public LLM tool",
    },
    "agent.shutdown": {
        "visibility": "internal",
        "executable": True,
        "notes": "agent runtime control endpoint action, not an LLM public tool",
    },
    "agent.disconnect": {
        "visibility": "legacy",
        "executable": False,
        "notes": "server endpoint exists, but current agent runtime does not implement this action",
    },
    "window.screencapture": {
        "visibility": "hidden",
        "executable": False,
        "notes": "no current implementation or schema; must not be advertised as a tool",
    },
    "screenshot": {
        "visibility": "hidden",
        "executable": False,
        "notes": "agent capability marker is unknown; no current public tool implementation",
    },
}

CONTROLLER_EXECUTABLE_TOOL_NAMES = {
    "system.list_tools",
    "system.get_last_run_summary",
    "tool.propose",
    "tool.list_proposals",
    "tool.get_proposal",
    "tool.update_proposal_status",
    "fs.resolve_path",
    "fs.open_folder",
    "fs.list_dir",
    "fs.stat",
    "fs.read_file",
    "fs.write_file",
    "fs.patch_file",
    "fs.rename",
    "fs.copy",
    "fs.move",
    "fs.delete",
    "memory.get_stats",
    "memory.list_facts",
    "device.get_passport",
    "device.refresh_state",
    "device.activate",
    "device.repair_activation",
    "device.check_runtime",
    "device.prepare_runtime",
    "device.repair_runtime",
    "window.list",
    "window.find",
    "window.verify",
    "window.focus",
    "window.close",
    "app.launch",
    "app.open_url",
    "app.open_file",
    "app.verify_launch",
    "app.close",
    "write_content",
    "execute_cmd",
    "get_file_link",
    "web_search",
    "remember_fact",
    "forget_fact",
    "answer.text",
    "answer.ask_clarification",
    "answer.report_failure",
    "answer.request_confirmation",
}

CONTROLLER_TRUST_INFERRED_ACTIONS = {
    "get_file_link",
    "write_content",
    "execute_cmd",
    "remember_fact",
    "forget_fact",
}


@dataclass(frozen=True)
class ToolInventoryItem:
    name: str
    source: str
    executable: bool
    registered: bool
    has_schema: bool
    has_contract: bool
    visibility: str
    notes: str = ""


def _controller_tool_schema_names() -> set[str]:
    names = set()
    try:
        from .controller_tools import TOOLS as controller_tools  # type: ignore
    except ImportError:
        try:
            from controller_tools import TOOLS as controller_tools  # type: ignore
        except ImportError:
            controller_tools = []
    for schema in controller_tools:
        fn_name = (schema.get("function") or {}).get("name")
        if fn_name:
            names.add(canonical_tool_name(fn_name))
    return names


def _device_tool_schema_names() -> set[str]:
    return {
        canonical_tool_name((schema.get("function") or {}).get("name", ""))
        for schema in DEVICE_TOOL_SCHEMAS
        if (schema.get("function") or {}).get("name")
    }


def _agent_action_names() -> set[str]:
    try:
        from agent.core.actions import ACTIONS  # type: ignore
    except Exception:
        return {
            "execute_cmd",
            "list_dir",
            "get_file_content",
            "write_content",
            "device.activate",
            "device.prepare_runtime",
            "device.refresh_state",
            "device.get_cached_passport",
            "window.list",
            "window.find",
            "window.verify",
            "window.focus",
            "window.close",
            "app.launch",
            "app.open_url",
            "app.verify_launch",
            "app.close",
            "agent.shutdown",
        }
    return set(ACTIONS.keys()) | {"agent.shutdown"}


def _source_for(name: str, *, metadata_names: set[str], device_schema_names: set[str], controller_schema_names: set[str], agent_action_names: set[str]) -> str:
    sources = []
    if name in metadata_names:
        sources.append("TOOL_METADATA")
    if name in device_schema_names:
        sources.append("DEVICE_TOOL_SCHEMAS")
    if name in controller_schema_names:
        sources.append("controller_tools")
    if name in CONTROLLER_EXECUTABLE_TOOL_NAMES:
        sources.append("controller_dispatch")
    if name in CONTROLLER_TRUST_INFERRED_ACTIONS:
        sources.append("controller_trust")
    if name in agent_action_names:
        sources.append("agent_actions")
    if name in LEGACY_OR_INTERNAL_ACTIONS:
        sources.append("legacy_inventory")
    return ",".join(sources) or "unknown"


def _visibility_for(name: str) -> str:
    if name in LEGACY_OR_INTERNAL_ACTIONS:
        return LEGACY_OR_INTERNAL_ACTIONS[name]["visibility"]
    meta = TOOL_METADATA.get(name) or {}
    visibility = meta.get("visibility")
    status = meta.get("status")
    if visibility in VISIBILITY_VALUES:
        return visibility
    if status == "hidden":
        return "hidden"
    if name in _public_system_list_tool_names():
        return "public"
    if name in CONTROLLER_EXECUTABLE_TOOL_NAMES:
        return "public"
    return "legacy"


def _public_system_list_tool_names() -> set[str]:
    names = set()
    for tools in list_tools("all").values():
        for tool in tools:
            if tool.get("name"):
                names.add(tool["name"])
    return names


def _notes_for(name: str) -> str:
    if name in LEGACY_OR_INTERNAL_ACTIONS:
        return LEGACY_OR_INTERNAL_ACTIONS[name]["notes"]
    if name == "get_file_link":
        return "internal download link tool; callable by controller loop but hidden from system.list_tools"
    if name == "web_search":
        return "implemented through configured Tavily integration"
    if name in {"remember_fact", "forget_fact"}:
        return "internal memory mutation tool guarded by trust checks"
    return ""


def build_tool_inventory() -> list[dict[str, Any]]:
    metadata_names = set(TOOL_METADATA)
    device_schema_names = _device_tool_schema_names()
    controller_schema_names = _controller_tool_schema_names()
    agent_action_names = _agent_action_names()
    names = (
        metadata_names
        | device_schema_names
        | controller_schema_names
        | agent_action_names
        | CONTROLLER_EXECUTABLE_TOOL_NAMES
        | CONTROLLER_TRUST_INFERRED_ACTIONS
        | set(LEGACY_OR_INTERNAL_ACTIONS)
    )

    inventory = []
    for name in sorted(n for n in names if n):
        visibility = _visibility_for(name)
        executable = bool(name in CONTROLLER_EXECUTABLE_TOOL_NAMES or name in agent_action_names)
        if name in LEGACY_OR_INTERNAL_ACTIONS:
            executable = bool(LEGACY_OR_INTERNAL_ACTIONS[name]["executable"])
        item = ToolInventoryItem(
            name=name,
            source=_source_for(
                name,
                metadata_names=metadata_names,
                device_schema_names=device_schema_names,
                controller_schema_names=controller_schema_names,
                agent_action_names=agent_action_names,
            ),
            executable=executable,
            registered=name in metadata_names,
            has_schema=name in device_schema_names or name in controller_schema_names,
            has_contract=get_tool_contract(name) is not None,
            visibility=visibility,
            notes=_notes_for(name),
        )
        inventory.append(asdict(item))
    return inventory

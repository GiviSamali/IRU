import json

from server.tool_contracts import (
    get_tool_contract,
    list_tool_contracts,
    normalize_tool_contract,
    validate_tool_contract,
)
from server.tool_inventory import build_tool_inventory
from server.tool_registry import DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name, list_tools
from server.controller_tools import WORKER_TOOLS


def test_all_existing_tool_metadata_can_produce_valid_contracts():
    contracts = list_tool_contracts()
    assert {contract["canonical_name"] for contract in contracts} == set(TOOL_METADATA)

    errors = {
        contract["canonical_name"]: validate_tool_contract(contract)
        for contract in contracts
    }

    assert errors == {name: [] for name in TOOL_METADATA}


def test_every_device_tool_schema_has_contract_or_known_alias():
    missing = []
    for schema in DEVICE_TOOL_SCHEMAS:
        function = schema.get("function") or {}
        name = function.get("name")
        canonical = canonical_tool_name(name)
        if not get_tool_contract(canonical):
            missing.append(name)

    assert missing == []


def test_validate_tool_contract_rejects_missing_required_fields():
    errors = validate_tool_contract({
        "name": "",
        "canonical_name": "",
        "category": "",
        "tool_type": "unknown",
        "purpose": "",
        "input_schema": [],
        "output_schema": [],
        "risk_level": "not-real",
        "permissions": "shell.execute",
        "evidence": None,
        "ui": None,
        "status": "not-real",
    })

    assert any("name is required" in error for error in errors)
    assert any("canonical_name is required" in error for error in errors)
    assert any("tool_type is not recognized" in error for error in errors)
    assert any("input_schema must be a dict" in error for error in errors)
    assert any("risk_level is not recognized" in error for error in errors)
    assert any("permissions must be a list" in error for error in errors)
    assert any("status is not recognized" in error for error in errors)


def test_execute_cmd_contract_marks_control_and_shell_permission():
    contract = get_tool_contract("execute_cmd")

    assert contract["risk_level"] == "fallback"
    assert "shell.execute" in contract["permissions"]
    assert contract["category"] == "control"
    assert contract["tool_type"] == "control"
    assert contract["ui"]["show_in_used_tools"] is True


def test_execute_cmd_schema_contains_first_class_outcome_guidance():
    schema = next(tool for tool in WORKER_TOOLS if tool["function"]["name"] == "execute_cmd")
    description = schema["function"]["description"]

    assert "OK:" in description
    assert "NO:" in description
    assert "ERROR:" in description
    assert "action" in description and "verification" in description
    assert "Visual/window verification is only needed" in description
    assert "write_content" in description
    assert "long, multiline" in description


def test_answer_text_contract_is_terminal_safe_and_visible():
    contract = get_tool_contract("answer.text")

    assert contract["risk_level"] == "safe"
    assert contract["tool_type"] == "answer"
    assert contract["category"] == "answer"
    assert "terminal" in contract["returns"]
    assert "terminal_answer_payload" in contract["evidence"]["produced"]
    assert contract["ui"]["show_in_used_tools"] is True


def test_write_content_contract_includes_file_write_permission():
    contract = get_tool_contract("write_content")

    assert contract["risk_level"] == "write"
    assert "file.write" in contract["permissions"]
    assert "creates_or_overwrites_file" in contract["side_effects"]
    assert "content" in contract["ui"]["sensitive_fields"]
    assert "content_sha256" in contract["evidence"]["produced"]
    assert "OK_summary" in contract["evidence"]["required_for_claims"]


def test_write_content_schema_describes_large_content_transport():
    schema = next(tool for tool in WORKER_TOOLS if tool["function"]["name"] == "write_content")
    description = schema["function"]["description"]

    assert "long, multiline, or generated text content" in description
    assert "instead of execute_cmd" in description
    assert "scripts, HTML, JSON, TXT, CSV, Markdown" in description
    assert "binary or Office documents" in description
    assert "chars_written" in description
    assert "bytes_written" in description
    assert "content_sha256" in description
    assert "OK/NO/ERROR" in description


def test_system_list_tools_remains_available_and_compact():
    registry = list_tools("all")
    payload = json.dumps(registry, ensure_ascii=False)

    assert "system" in registry
    assert any(tool["name"] == "system.list_tools" for tool in registry["system"])
    assert "contract_version" in registry["system"][0]
    assert "input_schema" not in payload
    assert "output_schema" not in payload
    assert "properties" not in payload


def test_contracts_do_not_contain_prompts_api_keys_or_secrets():
    forbidden = ["api_key", "authorization", "bearer ", "deepseek_api_key", "system_prompt", "tool schemas"]
    payload = json.dumps(list_tool_contracts(), ensure_ascii=False).lower()

    assert all(item not in payload for item in forbidden)


def test_normalize_tool_contract_accepts_partial_contract_without_crashing():
    normalized = normalize_tool_contract({
        "name": "demo.tool",
        "canonical_name": "demo.tool",
        "category": "demo",
        "tool_type": "typed",
        "label": "Demo",
        "purpose": "Demo purpose",
        "risk_level": "safe",
        "idempotency": "idempotent",
    })

    assert normalized["version"] == "v1"
    assert normalized["evidence"]["fresh_run_required"] is True
    assert normalized["ui"]["show_in_used_tools"] is True


def test_public_system_list_tools_have_contracts():
    missing = []
    for category_tools in list_tools("all").values():
        for tool in category_tools:
            if not get_tool_contract(tool["name"]):
                missing.append(tool["name"])

    assert missing == []


def test_public_executable_inventory_actions_are_registered_and_contracted():
    inventory = build_tool_inventory()
    offenders = [
        item
        for item in inventory
        if item["visibility"] == "public"
        and item["executable"]
        and (not item["registered"] or not item["has_contract"])
    ]

    assert offenders == []


def test_legacy_and_hidden_actions_are_explicitly_marked():
    inventory = {item["name"]: item for item in build_tool_inventory()}

    assert inventory["create_plan"]["visibility"] == "legacy"
    assert inventory["mark_step"]["visibility"] == "legacy"
    assert inventory["agent.disconnect"]["visibility"] == "legacy"
    assert inventory["window.screencapture"]["visibility"] == "hidden"
    assert inventory["window.screencapture"]["executable"] is False


def test_get_file_link_is_internal_contracted_download_tool_not_public_registry():
    inventory = {item["name"]: item for item in build_tool_inventory()}
    public_registry_names = {
        tool["name"]
        for tools in list_tools("all").values()
        for tool in tools
    }

    assert inventory["get_file_link"]["visibility"] == "internal"
    assert inventory["get_file_link"]["has_contract"] is True
    assert "artifact.download_link" in get_tool_contract("get_file_link")["permissions"]
    assert "get_file_link" not in public_registry_names


def test_web_search_is_implemented_and_contracted_but_screencapture_is_not_advertised():
    inventory = {item["name"]: item for item in build_tool_inventory()}
    public_registry_names = {
        tool["name"]
        for tools in list_tools("all").values()
        for tool in tools
    }

    assert inventory["web_search"]["executable"] is True
    assert inventory["web_search"]["registered"] is True
    assert inventory["web_search"]["has_contract"] is True
    assert "network.search" in get_tool_contract("web_search")["permissions"]
    assert "web_search" in public_registry_names
    assert "window.screencapture" not in public_registry_names


def test_open_url_and_last_run_summary_are_public_contracted_tools():
    inventory = {item["name"]: item for item in build_tool_inventory()}
    public_registry_names = {
        tool["name"]
        for tools in list_tools("all").values()
        for tool in tools
    }

    assert inventory["app.open_url"]["visibility"] == "public"
    assert inventory["app.open_url"]["executable"] is True
    assert inventory["app.open_url"]["has_contract"] is True
    assert "process.start" in get_tool_contract("app.open_url")["permissions"]
    assert "app.open_url" in public_registry_names

    assert inventory["system.get_last_run_summary"]["visibility"] == "public"
    assert inventory["system.get_last_run_summary"]["has_contract"] is True
    assert "system.get_last_run_summary" in public_registry_names


def test_app_open_url_is_available_to_pipeline_worker_toolset():
    worker_names = {tool["function"]["name"] for tool in WORKER_TOOLS}
    assert "app_open_url" in worker_names

import json

from server.tool_contracts import (
    get_tool_contract,
    list_tool_contracts,
    normalize_tool_contract,
    validate_tool_contract,
)
from server.tool_registry import DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name, list_tools


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


def test_execute_cmd_contract_marks_fallback_and_shell_permission():
    contract = get_tool_contract("execute_cmd")

    assert contract["risk_level"] == "fallback"
    assert "shell.execute" in contract["permissions"]
    assert contract["tool_type"] == "fallback"
    assert contract["ui"]["show_in_used_tools"] is True


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

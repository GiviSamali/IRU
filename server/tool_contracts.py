from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

try:
    from .tool_registry import CANONICAL_TOOL_NAMES, DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name
except ImportError:
    from tool_registry import CANONICAL_TOOL_NAMES, DEVICE_TOOL_SCHEMAS, TOOL_METADATA, canonical_tool_name  # type: ignore


TOOL_CONTRACT_VERSION = "v1"

TOOL_TYPES = {"system", "typed", "answer", "fallback", "proposal"}
RISK_LEVELS = {
    "safe",
    "read_only",
    "write",
    "runtime",
    "process_start",
    "process_control",
    "network",
    "destructive",
    "confirmation_required",
    "fallback",
}
IDEMPOTENCY_VALUES = {"idempotent", "safe_repeat", "not_idempotent", "unknown"}
STATUSES = {"active", "experimental", "deprecated", "hidden"}

REQUIRED_FIELDS = {
    "name",
    "canonical_name",
    "aliases",
    "category",
    "tool_type",
    "label",
    "purpose",
    "when_to_use",
    "when_not_to_use",
    "input_schema",
    "output_schema",
    "returns",
    "permissions",
    "risk_level",
    "side_effects",
    "evidence",
    "timeout_sec",
    "idempotency",
    "cleanup",
    "rollback",
    "examples",
    "test_plan",
    "ui",
    "version",
    "status",
}


@dataclass(frozen=True)
class EvidenceContract:
    produced: list[str] = field(default_factory=list)
    required_for_claims: list[str] = field(default_factory=list)
    fresh_run_required: bool = True


@dataclass(frozen=True)
class ToolUIContract:
    show_in_used_tools: bool = True
    show_details_by_default: bool = False
    sensitive_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolContract:
    name: str
    canonical_name: str
    aliases: list[str]
    category: str
    tool_type: str
    label: str
    purpose: str
    when_to_use: list[str]
    when_not_to_use: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    returns: str
    permissions: list[str]
    risk_level: str
    side_effects: list[str]
    evidence: EvidenceContract
    timeout_sec: int | None
    idempotency: str
    cleanup: str | None
    rollback: str | None
    examples: list[dict[str, Any]]
    test_plan: list[str]
    ui: ToolUIContract
    version: str = TOOL_CONTRACT_VERSION
    status: str = "active"


_SCHEMAS_BY_CANONICAL_NAME: dict[str, dict[str, Any]] = {
    canonical_tool_name((schema.get("function") or {}).get("name", "")): schema
    for schema in DEVICE_TOOL_SCHEMAS
    if (schema.get("function") or {}).get("name")
}


def _aliases_for(canonical_name: str) -> list[str]:
    aliases = [
        alias
        for alias, mapped in CANONICAL_TOOL_NAMES.items()
        if mapped == canonical_name and alias != canonical_name
    ]
    return sorted(set(aliases))


def _input_schema_for(canonical_name: str) -> dict[str, Any]:
    schema = _SCHEMAS_BY_CANONICAL_NAME.get(canonical_name) or {}
    fn = schema.get("function") or {}
    parameters = fn.get("parameters")
    return parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}


def _risk_from_danger(danger: str | None) -> str:
    value = (danger or "safe").strip().lower()
    if value == "safe":
        return "safe"
    if value == "write":
        return "write"
    if value == "write/runtime":
        return "runtime"
    if value == "process_start":
        return "process_start"
    if value in {"process_control", "window_focus"}:
        return "process_control"
    if value == "confirmation":
        return "confirmation_required"
    if value == "depends_on_command":
        return "fallback"
    if value in RISK_LEVELS:
        return value
    return "fallback" if "command" in value else "safe"


def _permissions_for(meta: dict[str, Any], canonical_name: str, risk_level: str) -> list[str]:
    danger = (meta.get("danger") or "").lower()
    category = meta.get("category") or ""
    permissions: set[str] = set()
    if canonical_name.startswith("answer."):
        permissions.add("answer.emit")
    if canonical_name.startswith("memory."):
        permissions.add("memory.read")
    if canonical_name.startswith("device."):
        permissions.add("device.read")
    if canonical_name in {"device.prepare_runtime", "device.repair_runtime"} or "runtime" in danger:
        permissions.update({"runtime.manage", "file.write"})
    if canonical_name == "write_content" or risk_level == "write" or category == "files":
        permissions.add("file.write")
    if canonical_name.startswith("window."):
        permissions.add("window.observe")
    if canonical_name in {"window.focus", "window.close", "app.close"} or risk_level == "process_control":
        permissions.add("process.control")
    if canonical_name.startswith("app.") or risk_level == "process_start":
        permissions.add("process.start")
    if risk_level == "fallback" or canonical_name == "execute_cmd":
        permissions.add("shell.execute")
    if canonical_name == "system.list_tools":
        permissions.add("tool_registry.read")
    return sorted(permissions)


def _side_effects_for(canonical_name: str, risk_level: str) -> list[str]:
    if canonical_name == "write_content":
        return ["creates_or_overwrites_file"]
    if canonical_name in {"device.prepare_runtime", "device.repair_runtime"}:
        return ["creates_or_modifies_managed_runtime", "writes_runtime_receipt"]
    if canonical_name == "device.activate":
        return ["writes_activation_receipt"]
    if canonical_name == "device.refresh_state":
        return ["updates_agent_state_snapshot", "updates_device_passport"]
    if canonical_name.startswith("app."):
        return ["starts_or_controls_process"]
    if canonical_name in {"window.focus", "window.close"}:
        return ["changes_window_state"]
    if canonical_name == "execute_cmd":
        return ["depends_on_command"]
    if risk_level in {"safe", "read_only"}:
        return []
    return ["may_change_device_state"]


def _evidence_for(canonical_name: str) -> EvidenceContract:
    if canonical_name == "answer.text":
        return EvidenceContract(
            produced=["terminal_answer_payload"],
            required_for_claims=["basis_step_ids_for_grounded_report"],
            fresh_run_required=True,
        )
    if canonical_name.startswith("answer."):
        return EvidenceContract(produced=["terminal_answer_payload"], required_for_claims=[], fresh_run_required=True)
    if canonical_name == "write_content":
        return EvidenceContract(produced=["file_path", "write_status"], required_for_claims=["file_path"], fresh_run_required=True)
    if canonical_name.startswith("window."):
        return EvidenceContract(produced=["window_match", "visibility_status"], required_for_claims=["window_match"], fresh_run_required=True)
    if canonical_name.startswith("app."):
        return EvidenceContract(produced=["pid", "launch_status", "window_verification"], required_for_claims=["pid_or_window"], fresh_run_required=True)
    if canonical_name.startswith("device."):
        return EvidenceContract(produced=["device_summary", "context_handle"], required_for_claims=["current_run_tool_result"], fresh_run_required=True)
    if canonical_name.startswith("memory."):
        return EvidenceContract(produced=["memory_summary"], required_for_claims=["current_run_tool_result"], fresh_run_required=True)
    if canonical_name == "execute_cmd":
        return EvidenceContract(produced=["returncode", "stdout", "stderr"], required_for_claims=["returncode"], fresh_run_required=True)
    return EvidenceContract(produced=["tool_result"], required_for_claims=["current_run_tool_result"], fresh_run_required=True)


def _idempotency_for(canonical_name: str, risk_level: str) -> str:
    if canonical_name == "write_content":
        return "not_idempotent"
    if canonical_name in {"device.prepare_runtime", "device.repair_runtime", "device.activate", "device.repair_activation"}:
        return "safe_repeat"
    if canonical_name in {"app.launch", "window.close", "app.close", "execute_cmd"}:
        return "unknown"
    if risk_level in {"safe", "read_only"}:
        return "idempotent"
    return "unknown"


def _when_not_to_use(canonical_name: str, meta: dict[str, Any]) -> list[str]:
    if canonical_name == "execute_cmd":
        return ["a typed tool can perform the task", "action needs structured evidence and a typed tool exists"]
    if canonical_name == "write_content":
        return ["binary files", "Office documents", "when shell execution is specifically needed"]
    if canonical_name == "answer.text":
        return ["before required tool evidence is collected", "together with another action tool in the same iteration"]
    if canonical_name.startswith("window."):
        return ["file system checks", "process launch without window verification"]
    if canonical_name.startswith("device."):
        return ["local file editing", "raw shell tasks unrelated to device state"]
    return []


def _examples_for(canonical_name: str) -> list[dict[str, Any]]:
    if canonical_name == "write_content":
        return [{"input": {"path": "C:/Users/user/Desktop/note.txt", "content": "hello"}, "claims": ["file_path"]}]
    if canonical_name == "window.verify":
        return [{"input": {"title_contains": "IRU PyQt Smoke", "require_visible": True}, "claims": ["window visible"]}]
    if canonical_name == "answer.text":
        return [{"input": {"answer_type": "grounded_report", "text": "Готово", "basis": ["step_1"]}, "terminal": True}]
    return []


def _test_plan_for(canonical_name: str) -> list[str]:
    plan = ["validate contract shape", "verify tool appears in system.list_tools when active"]
    if canonical_name == "answer.text":
        plan.append("verify terminal answer validation")
    if canonical_name == "execute_cmd":
        plan.append("verify fallback is not preferred over typed tools")
    return plan


def _ui_for(canonical_name: str) -> ToolUIContract:
    sensitive_fields = ["command"] if canonical_name == "execute_cmd" else []
    return ToolUIContract(
        show_in_used_tools=True,
        show_details_by_default=False,
        sensitive_fields=sensitive_fields,
    )


def build_contract_from_existing_registry(tool_name: str) -> dict[str, Any]:
    canonical_name = canonical_tool_name(tool_name)
    meta = TOOL_METADATA.get(canonical_name, {})
    risk_level = _risk_from_danger(meta.get("danger"))
    returns = str(meta.get("returns") or "tool result")
    contract = ToolContract(
        name=canonical_name,
        canonical_name=canonical_name,
        aliases=_aliases_for(canonical_name),
        category=str(meta.get("category") or "other"),
        tool_type=str(meta.get("tool_type") or "typed"),
        label=str(meta.get("tool_label") or canonical_name),
        purpose=str(meta.get("purpose") or ""),
        when_to_use=list(meta.get("when_to_use") or []),
        when_not_to_use=_when_not_to_use(canonical_name, meta),
        input_schema=_input_schema_for(canonical_name),
        output_schema={"type": "object", "description": returns},
        returns=returns,
        permissions=_permissions_for(meta, canonical_name, risk_level),
        risk_level=risk_level,
        side_effects=_side_effects_for(canonical_name, risk_level),
        evidence=_evidence_for(canonical_name),
        timeout_sec=None,
        idempotency=_idempotency_for(canonical_name, risk_level),
        cleanup=None,
        rollback=None,
        examples=_examples_for(canonical_name),
        test_plan=_test_plan_for(canonical_name),
        ui=_ui_for(canonical_name),
        version=TOOL_CONTRACT_VERSION,
        status="active",
    )
    return asdict(contract)


def normalize_tool_contract(raw: dict[str, Any] | ToolContract) -> dict[str, Any]:
    if isinstance(raw, ToolContract):
        return asdict(raw)
    if is_dataclass(raw):
        return asdict(raw)
    normalized = dict(raw or {})
    normalized.setdefault("aliases", [])
    normalized.setdefault("when_to_use", [])
    normalized.setdefault("when_not_to_use", [])
    normalized.setdefault("input_schema", {})
    normalized.setdefault("output_schema", {})
    normalized.setdefault("permissions", [])
    normalized.setdefault("side_effects", [])
    normalized.setdefault("examples", [])
    normalized.setdefault("test_plan", [])
    normalized.setdefault("version", TOOL_CONTRACT_VERSION)
    normalized.setdefault("status", "active")
    evidence = normalized.get("evidence")
    if isinstance(evidence, EvidenceContract):
        evidence = asdict(evidence)
    normalized["evidence"] = {
        "produced": list((evidence or {}).get("produced") or []),
        "required_for_claims": list((evidence or {}).get("required_for_claims") or []),
        "fresh_run_required": bool((evidence or {}).get("fresh_run_required", True)),
    }
    ui = normalized.get("ui")
    if isinstance(ui, ToolUIContract):
        ui = asdict(ui)
    normalized["ui"] = {
        "show_in_used_tools": bool((ui or {}).get("show_in_used_tools", True)),
        "show_details_by_default": bool((ui or {}).get("show_details_by_default", False)),
        "sensitive_fields": list((ui or {}).get("sensitive_fields") or []),
    }
    return normalized


def validate_tool_contract(contract: dict[str, Any] | ToolContract) -> list[str]:
    try:
        normalized = normalize_tool_contract(contract)
    except Exception as exc:
        return [f"contract is not normalizable: {type(exc).__name__}: {exc}"]

    errors: list[str] = []
    for field_name in sorted(REQUIRED_FIELDS):
        if field_name not in normalized:
            errors.append(f"missing required field: {field_name}")

    for field_name in ("name", "canonical_name", "category", "tool_type", "purpose", "risk_level", "status"):
        if not str(normalized.get(field_name) or "").strip():
            errors.append(f"{field_name} is required")

    if normalized.get("tool_type") not in TOOL_TYPES:
        errors.append(f"tool_type is not recognized: {normalized.get('tool_type')}")
    if not isinstance(normalized.get("input_schema"), dict):
        errors.append("input_schema must be a dict")
    if not isinstance(normalized.get("output_schema"), dict):
        errors.append("output_schema must be a dict")
    if normalized.get("risk_level") not in RISK_LEVELS:
        errors.append(f"risk_level is not recognized: {normalized.get('risk_level')}")
    if not isinstance(normalized.get("permissions"), list):
        errors.append("permissions must be a list")
    if not isinstance(normalized.get("evidence"), dict):
        errors.append("evidence must be an object")
    if not isinstance(normalized.get("ui"), dict):
        errors.append("ui must be an object")
    if normalized.get("idempotency") not in IDEMPOTENCY_VALUES:
        errors.append(f"idempotency is not recognized: {normalized.get('idempotency')}")
    if normalized.get("status") not in STATUSES:
        errors.append(f"status is not recognized: {normalized.get('status')}")
    return errors


def get_tool_contract(tool_name: str) -> dict[str, Any] | None:
    canonical_name = canonical_tool_name(tool_name)
    if canonical_name not in TOOL_METADATA:
        return None
    return build_contract_from_existing_registry(canonical_name)


def list_tool_contracts(category: str = "all") -> list[dict[str, Any]]:
    requested = (category or "all").strip().lower()
    contracts = []
    for name, meta in TOOL_METADATA.items():
        if requested != "all" and (meta.get("category") or "other") != requested:
            continue
        contracts.append(build_contract_from_existing_registry(name))
    return contracts

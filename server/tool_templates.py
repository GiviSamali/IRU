import copy
import json


RISK_LEVELS = {"read_only", "state_changing", "destructive", "privileged"}
POLICY_DECISIONS = {"allow", "confirm", "deny"}
REQUIRED_FIELDS = set(
    "id title description category version risk_level input_schema output_schema policy "
    "implementation_notes verification audit tests failure_modes examples limitations".split()
)


PYTHON_ENV_CHECK_TEMPLATE = {
    "id": "python.env_check",
    "title": "Python Environment Check",
    "description": "Check the Python environment on a device without changing state.",
    "category": "python",
    "version": "1.0.0",
    "risk_level": "read_only",
    "input_schema": {
        "type": "object",
        "properties": {
            "packages": {"type": "array", "items": {"type": "string"}, "optional": True},
            "require_pip": {"type": "boolean", "optional": True},
            "interpreter_hint": {"type": "string", "optional": True},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "python_found": "bool", "python_version": "string | null", "python_path": "string | null",
            "pip_found": "bool | null", "pip_version": "string | null",
            "packages": "dict package_name -> installed/missing/error",
            "status": "ok | python_missing | pip_missing | dependency_missing | error",
            "message": "string",
        },
    },
    "policy": {"decision": "allow"},
    "implementation_notes": [
        "Use the discovered interpreter consistently.", "Missing package is not missing Python.",
        "Do not install dependencies.",
        "Do not continue searching interpreters after a working interpreter is found unless explicitly requested.",
        "No state-changing operations.",
    ],
    "verification": [
        "Python is found if interpreter returns version and sys.executable successfully.",
        "Check pip via interpreter -m pip --version.",
        'Check package via interpreter -c "import package".',
        "ModuleNotFoundError means dependency_missing for that package.",
    ],
    "audit": ["Read-only environment check.", "Log device_id, interpreter path, packages requested, and result status.", "Do not log secrets."],
    "tests": [
        "Python found", "Python missing", "Pip found", "Pip missing", "Package installed", "Package missing",
        "Missing package does not become python_missing",
    ],
    "failure_modes": ["python_missing", "pip_missing", "dependency_missing", "permission_error", "timeout", "unexpected_output"],
    "examples": [{"input": {"packages": ["fastapi"], "require_pip": True}, "status": "ok"}],
    "limitations": [
        "Does not install packages.", "Does not modify PATH.", "Does not create venv.",
        "Does not solve broken Python installs automatically.",
    ],
}

_TEMPLATES = [PYTHON_ENV_CHECK_TEMPLATE]


def list_tool_templates() -> list[dict]:
    return copy.deepcopy(_TEMPLATES)


def get_tool_template(template_id: str) -> dict | None:
    for template in _TEMPLATES:
        if template["id"] == template_id:
            return copy.deepcopy(template)
    return None


def _policy_decision(policy: object) -> object:
    if isinstance(policy, str):
        return policy
    if isinstance(policy, dict):
        return policy.get("decision")
    return None


def validate_tool_template(template: dict) -> list[str]:
    errors = []
    missing_fields = sorted(REQUIRED_FIELDS - set(template))
    errors.extend(f"Missing required field: {field}" for field in missing_fields)

    if "risk_level" in template and template["risk_level"] not in RISK_LEVELS:
        errors.append(f"Invalid risk_level: {template['risk_level']}")

    if "policy" in template:
        decision = _policy_decision(template["policy"])
        if decision not in POLICY_DECISIONS:
            errors.append(f"Invalid policy decision: {decision}")

    for schema_field in ("input_schema", "output_schema"):
        if not template.get(schema_field):
            errors.append(f"Missing or empty {schema_field}")

    tests = template.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("Template tests must contain at least one test case")

    return errors


def render_tool_template(template: dict) -> str:
    policy = template.get("policy", {})
    decision = _policy_decision(policy)
    return "\n".join([
        f"# {template.get('title', 'Untitled Tool Template')}",
        f"- id: {template.get('id')}",
        f"- category: {template.get('category')}",
        f"- version: {template.get('version')}",
        f"- risk_level: {template.get('risk_level')}",
        f"- policy: {decision}",
        "",
        "## Description",
        str(template.get("description", "")),
        "",
        "## Input Schema",
        json.dumps(template.get("input_schema", {}), indent=2, sort_keys=True),
        "",
        "## Output Schema",
        json.dumps(template.get("output_schema", {}), indent=2, sort_keys=True),
        "",
        "## Verification",
        "\n".join(f"- {item}" for item in template.get("verification", [])),
        "",
        "## Failure Modes",
        "\n".join(f"- {item}" for item in template.get("failure_modes", [])),
        "",
        "## Limitations",
        "\n".join(f"- {item}" for item in template.get("limitations", [])),
    ])

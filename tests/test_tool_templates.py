from server import tool_templates


def test_list_tool_templates_contains_python_env_check():
    ids = {template["id"] for template in tool_templates.list_tool_templates()}

    assert "python.env_check" in ids


def test_get_tool_template_returns_python_env_check():
    template = tool_templates.get_tool_template("python.env_check")

    assert template is not None
    assert template["title"] == "Python Environment Check"


def test_get_tool_template_unknown_returns_none():
    assert tool_templates.get_tool_template("unknown.template") is None


def test_validate_tool_template_passes_for_python_env_check():
    assert tool_templates.validate_tool_template(tool_templates.get_tool_template("python.env_check")) == []


def test_validate_tool_template_reports_missing_required_fields():
    errors = tool_templates.validate_tool_template({"id": "incomplete"})

    assert "Missing required field: title" in errors
    assert "Missing required field: input_schema" in errors
    assert "Template tests must contain at least one test case" in errors


def test_validate_tool_template_rejects_invalid_risk_level():
    template = tool_templates.get_tool_template("python.env_check")
    template["risk_level"] = "unsafe"

    assert "Invalid risk_level: unsafe" in tool_templates.validate_tool_template(template)


def test_validate_tool_template_rejects_invalid_policy_decision():
    template = tool_templates.get_tool_template("python.env_check")
    template["policy"] = {"decision": "maybe"}

    assert "Invalid policy decision: maybe" in tool_templates.validate_tool_template(template)


def test_python_env_check_has_input_schema_and_output_schema():
    template = tool_templates.get_tool_template("python.env_check")
    assert template["input_schema"]
    assert template["output_schema"]


def test_python_env_check_has_tests_failure_modes_and_limitations():
    template = tool_templates.get_tool_template("python.env_check")
    assert template["tests"]
    assert template["failure_modes"]
    assert template["limitations"]


def test_render_tool_template_contains_expected_sections():
    rendered = tool_templates.render_tool_template(tool_templates.get_tool_template("python.env_check"))

    assert "Python Environment Check" in rendered
    assert "risk_level: read_only" in rendered
    assert "policy: allow" in rendered
    assert "Input Schema" in rendered
    assert "Output Schema" in rendered
    assert "Verification" in rendered
    assert "Failure Modes" in rendered

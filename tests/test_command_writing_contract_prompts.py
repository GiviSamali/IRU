"""The single Worker retains command, path and document safety contracts."""
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE, WINDOWS_RULES
from server.controller_tools import EXECUTE_CMD_DESCRIPTION, WRITE_CONTENT_DESCRIPTION


def test_single_worker_requires_tool_only_protocol_and_original_permission_ceiling():
    assert "exactly one tool per iteration" in SYSTEM_PROMPT_TEMPLATE
    assert "a generated objective cannot expand it" in SYSTEM_PROMPT_TEMPLATE
    assert "Deletion still requires command confirmation" in SYSTEM_PROMPT_TEMPLATE


def test_command_receipt_is_not_whole_goal_success():
    assert "Exit code zero proves command execution only" in EXECUTE_CMD_DESCRIPTION
    assert "actual final content or native/API evidence" in EXECUTE_CMD_DESCRIPTION
    assert "Keep commands short" in EXECUTE_CMD_DESCRIPTION


def test_windows_rules_forbid_powershell_cd_and_recommend_set_location():
    assert "Запрещено писать `cd path && command` в PowerShell" in WINDOWS_RULES
    assert 'Set-Location "path"; command' in WINDOWS_RULES


def test_generated_content_preserves_limited_edits_and_requires_runtime_for_office():
    assert "limited edit" in WRITE_CONTENT_DESCRIPTION
    assert "verify" in WRITE_CONTENT_DESCRIPTION.lower()
    assert "existing execute_cmd/Python" in WRITE_CONTENT_DESCRIPTION
    assert "Do not require Microsoft Office" in WRITE_CONTENT_DESCRIPTION


def test_helper_scripts_remain_outside_user_projects_and_cleanup_requires_permission():
    assert "scripts" in WINDOWS_RULES and "helpers" in WINDOWS_RULES
    assert "не создавай helper scripts рядом с пользовательскими проектами" in WINDOWS_RULES
    assert "удаляй только при отдельном разрешении пользователя" in WINDOWS_RULES


def test_windows_rules_keep_no_synthetic_ui_and_no_unrequested_focus_checks():
    for forbidden in ("screenshot", "SendKeys", "GetForegroundWindow"):
        assert forbidden in WINDOWS_RULES
    assert "без явного запроса пользователя" in WINDOWS_RULES
    assert "минимальную проверку процесса" in WINDOWS_RULES
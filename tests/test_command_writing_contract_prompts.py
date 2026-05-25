from server.controller_pipeline import pipeline_worker_prompt
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE, WINDOWS_RULES


def _shared_context():
    return {
        "current_device_id": "device-1",
        "current_hostname": "devbox",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "target_device_id": "device-1",
        "os_rules": WINDOWS_RULES,
        "current_datetime_msk": "2026-05-11 12:00",
    }


def test_system_prompt_contains_command_execution_contract():
    assert "Контракт выполнения команд" in SYSTEM_PROMPT_TEMPLATE
    assert "OK, ERROR, EXISTS, CREATED, PY_COMPILE_OK, APP_STARTED" in SYSTEM_PROMPT_TEMPLATE


def test_windows_rules_forbid_powershell_cd_and_recommend_set_location():
    assert "Запрещено писать `cd path && command` в PowerShell" in WINDOWS_RULES
    assert 'Set-Location "path"; command' in WINDOWS_RULES
    assert 'cmd /c "cd /d path && command"' in WINDOWS_RULES


def test_windows_rules_use_long_running_for_gui():
    assert "long_running=true" in WINDOWS_RULES
    assert "Timeout GUI-процесса не считай обычной ошибкой" in WINDOWS_RULES


def test_prompts_keep_document_helper_scripts_under_iru_home():
    assert "%LOCALAPPDATA%\\IRU\\scripts\\helpers" in SYSTEM_PROMPT_TEMPLATE
    assert "$env:LOCALAPPDATA\\IRU\\scripts\\helpers" in WINDOWS_RULES


def test_windows_rules_forbid_gui_screen_and_focus_checks_without_request():
    for forbidden in ("screenshot", "SendKeys", "GetForegroundWindow"):
        assert forbidden in WINDOWS_RULES
    assert "без явного запроса пользователя" in WINDOWS_RULES
    assert "минимальную проверку процесса" in WINDOWS_RULES


def test_pipeline_worker_prompt_requires_minimal_commands_and_observable_result():
    prompt = pipeline_worker_prompt(
        _shared_context(),
        "goal",
        {"title": "step", "instruction": "do it", "device_id": "device-1"},
        [],
    )

    assert "Выполняй минимальный набор команд" in prompt
    assert "Каждая команда должна иметь наблюдаемый результат" in prompt
    assert "PY_COMPILE_OK" in prompt
    assert "py_compile успешен и нужные файлы созданы" in prompt
    assert "app_launch" in prompt
    assert "%LOCALAPPDATA%\\IRU\\scripts\\helpers" in prompt


def test_pipeline_worker_prompt_forbids_gui_screen_and_focus_checks_without_request():
    prompt = pipeline_worker_prompt(
        _shared_context(),
        "goal",
        {"title": "step", "instruction": "do it", "device_id": "device-1"},
        [],
    )

    for forbidden in ("screenshot", "SendKeys", "GetForegroundWindow"):
        assert forbidden in prompt
    assert "без явного запроса пользователя" in prompt

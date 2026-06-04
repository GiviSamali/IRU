from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_renderer_can_show_one_or_many_used_tools():
    source = (ROOT / "ui" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "function renderUsedToolsLine" in source
    assert "Использован инструмент:" in source
    assert "Использованы инструменты:" in source
    assert "Использован fallback:" in source
    assert "command.tool_name" in source
    assert "window_title" in source
    assert "process_alive" in source
    assert "process_name" in source
    assert "command.tool_type !== 'answer'" in source
    assert "parts.push('Ответ')" in source
    assert "answer_type" in source
    assert "self_check" in source


def test_chat_live_status_uses_safe_status_contract():
    chat_source = (ROOT / "ui" / "js" / "chat.js").read_text(encoding="utf-8")
    extras_source = (ROOT / "ui" / "js" / "extras.js").read_text(encoding="utf-8")

    assert "const SAFE_TASK_STATUS_LABELS" in chat_source
    assert "function normalizeTaskStatusLabel" in chat_source
    assert "function deriveLiveTaskStatus" in chat_source
    assert "SAFE_TASK_STATUS_LABELS.running" in chat_source
    assert "normalizeTaskStatusLabel(m.currentStatus || 'thinking')" in chat_source
    assert "deriveLiveTaskStatus(task, msg)" in chat_source
    assert "msg.currentStep = task.current_step" not in chat_source
    assert "task.current_step && msg.currentStep" not in chat_source
    assert "currentStep:" not in extras_source
    assert "currentStatus: 'thinking'" in extras_source


def test_device_passport_buttons_show_used_typed_tools():
    source = (ROOT / "ui" / "js" / "devices.js").read_text(encoding="utf-8")

    assert "Паспорт" in source
    assert "device-passport-toggle" in source
    assert "function renderSelectedDeviceHeader" in source
    assert "device-passport-section-title" in source
    assert "data-action=\"passport-close\"" in source
    assert "Использован инструмент: device.refresh_state" in source
    assert "Использован инструмент: device.activate" in source
    assert "Использован инструмент: device.repair_activation" in source
    assert "device.prepare_runtime" in source


def test_device_passport_runtime_prepare_disconnect_message():
    source = (ROOT / "ui" / "js" / "devices.js").read_text(encoding="utf-8")

    assert "runtime_prepare_interrupted" in source
    assert "function gpuValue" in source
    assert "function snapshotSourceLabel" in source
    assert "state_snapshot_source" in source
    assert "gpu_summary" in source
    assert "GPU:" in source
    assert "свежий снимок" in source
    assert "кэш агента" in source
    assert "Снимок ещё не собирался" in source
    assert "—" in source
    assert "Подготовка прервана. Агент переподключился — нажмите Проверить runtime." in source

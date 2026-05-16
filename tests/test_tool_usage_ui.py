from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_renderer_can_show_one_or_many_used_tools():
    source = (ROOT / "ui" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "function renderUsedToolsLine" in source
    assert "Использован инструмент:" in source
    assert "Использованы инструменты:" in source
    assert "Использован fallback:" in source
    assert "command.tool_name" in source


def test_device_passport_buttons_show_used_typed_tools():
    source = (ROOT / "ui" / "js" / "devices.js").read_text(encoding="utf-8")

    assert "Использован инструмент: device.refresh_state" in source
    assert "Использован инструмент: device.activate" in source
    assert "Использован инструмент: device.repair_activation" in source

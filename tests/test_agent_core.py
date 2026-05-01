import asyncio
import importlib
import logging
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _make_agent_paths(tmp_path):
    from core.config import AgentPaths

    config_dir = tmp_path / "agent-config"
    logs_dir = config_dir / "logs"
    return AgentPaths(
        base_dir=tmp_path,
        config_dir=config_dir,
        config_path=config_dir / "config.json",
        legacy_config_path=tmp_path / "legacy-config.json",
        logs_dir=logs_dir,
        log_path=logs_dir / "agent.log",
        source_icon_path=tmp_path / "IruIcon.ico",
    )


def test_load_config_reads_utf8_sig_bom_file(tmp_path):
    from core.config import load_config

    paths = _make_agent_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(
        '{"device_id":"device-1","user_token":"token-1","server_url":"wss://example.test"}',
        encoding="utf-8-sig",
    )

    config = load_config(paths)

    assert config["device_id"] == "device-1"
    assert config["user_token"] == "token-1"
    assert config["server_url"] == "wss://example.test"


def test_load_config_fills_current_defaults_for_missing_optional_fields(tmp_path):
    from core.config import DEFAULT_CONFIG, load_config

    paths = _make_agent_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(
        '{"device_id":"device-2","user_token":"token-2"}',
        encoding="utf-8",
    )

    config = load_config(paths)

    assert config["device_id"] == "device-2"
    assert config["user_token"] == "token-2"
    assert config["server_url"] == DEFAULT_CONFIG["server_url"]


def test_platform_detection_returns_windows_module_for_windows(monkeypatch):
    import platforms as platforms_module

    monkeypatch.setattr(platforms_module._platform, "system", lambda: "Windows")

    platform_mod = platforms_module.get_platform()

    assert platform_mod.name == "Windows"


def test_platform_detection_falls_back_to_linux_for_unknown_system(monkeypatch):
    import platforms as platforms_module

    monkeypatch.setattr(platforms_module._platform, "system", lambda: "Darwin")

    platform_mod = platforms_module.get_platform()

    assert platform_mod.name == "Linux"


def test_action_dispatcher_reports_unknown_action():
    from core.runtime import AgentRuntime
    from core.state import AgentSnapshot, AgentState

    runtime = AgentRuntime(
        config={"device_id": "dev", "server_url": "ws://example.test", "user_token": "token"},
        agent_version="test-version",
        logger=logging.getLogger("agent-test"),
        state=AgentState(
            AgentSnapshot(
                version="test-version",
                device_id="dev",
                server_url="ws://example.test",
                config_path="config.json",
                logs_dir="logs",
                log_path="logs/agent.log",
            )
        ),
    )

    result = asyncio.run(runtime._handle_command({"id": "cmd-1", "action": "unknown_action", "params": {}}))

    assert result["id"] == "cmd-1"
    assert result["status"] == "error"
    assert "Неизвестное действие" in result["error"]

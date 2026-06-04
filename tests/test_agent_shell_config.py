import json

from agent.shell import config as shell_config


def test_resolve_web_url_prefers_env(monkeypatch, tmp_path):
    config_path = tmp_path / "state" / "shell_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"web_url": "http://from-config"}), encoding="utf-8")
    monkeypatch.setenv("IRU_WEB_URL", "https://irumode.ru")

    assert shell_config.resolve_web_url(config_path) == "https://irumode.ru"


def test_resolve_web_url_uses_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("IRU_WEB_URL", raising=False)
    config_path = tmp_path / "state" / "shell_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"web_url": "http://from-config"}), encoding="utf-8")

    assert shell_config.resolve_web_url(config_path) == "http://from-config"


def test_resolve_web_url_writes_and_uses_default(monkeypatch, tmp_path):
    monkeypatch.delenv("IRU_WEB_URL", raising=False)
    monkeypatch.setenv("IRU_HOME", str(tmp_path))

    assert shell_config.resolve_web_url() == shell_config.DEFAULT_WEB_URL

    config_path = tmp_path / "state" / "shell_config.json"
    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["web_url"] == shell_config.DEFAULT_WEB_URL


def test_get_shell_config_path_prefers_existing_state_then_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("IRU_HOME", str(tmp_path))
    legacy_path = tmp_path / "shell_config.json"
    legacy_path.write_text(json.dumps({"web_url": "http://legacy"}), encoding="utf-8")

    assert shell_config.get_shell_config_path() == legacy_path

    state_path = tmp_path / "state" / "shell_config.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"web_url": "http://state"}), encoding="utf-8")

    assert shell_config.get_shell_config_path() == state_path


def test_shell_config_does_not_keep_auth_secrets(tmp_path):
    config_path = tmp_path / "state" / "shell_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "web_url": "http://from-config",
            "token": "secret",
            "password": "secret",
            "access_token": "secret",
            "refresh_token": "secret",
        }),
        encoding="utf-8",
    )

    loaded = shell_config.load_shell_config(config_path)

    assert loaded["web_url"] == "http://from-config"
    for key in shell_config.SECRET_CONFIG_KEYS:
        assert key not in loaded

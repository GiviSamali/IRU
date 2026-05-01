import importlib

import pytest
from fastapi.testclient import TestClient


def _clear_runtime_state():
    import server.runtime_state as runtime_state_module

    for attr_name in ("devices", "tasks", "download_tokens", "declined_plan_requests", "declined_suggested_facts", "rate_counters", "ip_rate_counters"):
        value = getattr(runtime_state_module, attr_name, None)
        if hasattr(value, "clear"):
            value.clear()


@pytest.fixture(autouse=True)
def reset_runtime_state():
    _clear_runtime_state()
    yield
    _clear_runtime_state()


@pytest.fixture
def client(tmp_path, monkeypatch, reset_runtime_state):
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("IRU_DB_PATH", str(db_path))

    import server.database as database_module
    import server.main as main_module

    importlib.reload(database_module)
    importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client

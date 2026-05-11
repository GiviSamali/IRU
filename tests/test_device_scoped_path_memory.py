import asyncio

import pytest

from server.controller import LLMRuntimeContext, _build_non_pipeline_system_prompt
from server.controller_pipeline import build_pipeline_worker_context, pipeline_worker_prompt
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE
from server.controller_shared import build_memory_block
from server.path_scope import PATH_SCOPE_ERROR, filter_memory_facts_for_device, resolve_relative_preference


def _create_user(name="path-scope-user"):
    from server.database import create_user

    return create_user(name)


def _target_profile(user_id, device_id="device-new", machine_guid="machine-new"):
    from server.database import upsert_device_profile

    profile = {
        "hostname": "newbox",
        "os": "Windows",
        "os_version": "11",
        "username": "User",
        "desktop_path": r"C:\Users\User\Desktop",
        "machine_guid": machine_guid,
    }
    upsert_device_profile(device_id, user_id, profile)
    return profile


def test_user_memory_absolute_path_is_not_exposed_to_worker_prompt(client):
    from server.database import add_user_fact

    user = _create_user()
    _target_profile(user["id"])
    add_user_fact(str(user["id"]), r"Use C:\Users\russa\TEST_IRU for projects", "preference")
    all_devices = {
        "device-new": {
            "info": {
                "hostname": "newbox",
                "os": "Windows",
                "os_version": "11",
                "username": "User",
                "home": r"C:\Users\User",
                "desktop_path": r"C:\Users\User\Desktop",
                "machine_guid": "machine-new",
            }
        }
    }

    shared, _ = build_pipeline_worker_context(
        target_device_id="device-new",
        current_device_id="device-new",
        current_device_info=all_devices["device-new"]["info"],
        all_devices=all_devices,
        current_device_profile=_target_profile(user["id"]),
        mem_user_id=str(user["id"]),
        windows_rules="windows",
        linux_rules="linux",
    )
    prompt = pipeline_worker_prompt(shared, "goal", {"title": "step", "instruction": "do it"}, [])

    assert r"C:\Users\russa\TEST_IRU" not in prompt
    assert "username: User" in prompt
    assert r"home_path: C:\Users\User" in prompt
    assert r"desktop_path: C:\Users\User\Desktop" in prompt


def test_non_pipeline_prompt_contains_target_device_context(client):
    user = _create_user("path-scope-non-pipeline")
    profile = _target_profile(user["id"])
    info = {"hostname": "newbox", "os": "Windows", "os_version": "11", "home": r"C:\Users\User"}
    from server.controller_shared import build_device_profile_block
    from server.path_scope import build_target_device_block

    runtime = LLMRuntimeContext(
        cfg={},
        os_info="Windows",
        hostname="newbox",
        os_version="11",
        devices_block="device-new",
        profile_block=build_device_profile_block(profile),
        memory_block="",
        target_device_block=build_target_device_block("", info, profile),
        os_rules="windows",
        current_datetime_msk="2026-05-11 12:00",
        machine_guid="machine-new",
        mem_user_id=str(user["id"]),
    )

    prompt = _build_non_pipeline_system_prompt(runtime=runtime, device_id="device-new")

    assert "Target device context" in prompt
    assert "device_id: device-new" in prompt
    assert "username: User" in prompt
    assert r"home_path: C:\Users\User" in prompt


def test_write_content_blocks_other_windows_user_profile_path(client):
    from server.database import create_user, upsert_device_profile
    from server.runtime_state import devices
    from server.task_runtime import send_command_to_agent

    user = create_user("path-guard-user")
    device_key = f"{user['id']}:device-1"
    info = {"hostname": "newbox", "os": "Windows", "username": "User", "home": r"C:\Users\User"}
    upsert_device_profile("device-1", user["id"], {**info, "desktop_path": r"C:\Users\User\Desktop", "machine_guid": "machine-1"})
    devices[device_key] = {"user_id": user["id"], "info": info, "pending": {}}

    with pytest.raises(RuntimeError, match=PATH_SCOPE_ERROR):
        asyncio.run(send_command_to_agent(device_key, "write_content", {"path": r"C:\Users\russa\TEST_IRU\file.txt", "content": "x"}, user_id=user["id"]))


def test_execute_cmd_blocks_creating_other_windows_user_profile_path(client):
    from server.database import create_user, upsert_device_profile
    from server.runtime_state import devices
    from server.task_runtime import send_command_to_agent

    user = create_user("path-guard-exec-user")
    device_key = f"{user['id']}:device-1"
    info = {"hostname": "newbox", "os": "Windows", "username": "User", "home": r"C:\Users\User"}
    upsert_device_profile("device-1", user["id"], {**info, "desktop_path": r"C:\Users\User\Desktop", "machine_guid": "machine-1"})
    devices[device_key] = {"user_id": user["id"], "info": info, "pending": {}}
    cmd = r'if (!(Test-Path "C:\Users\russa\TEST_IRU")) { New-Item -ItemType Directory "C:\Users\russa\TEST_IRU" }'

    try:
        with pytest.raises(RuntimeError, match=PATH_SCOPE_ERROR):
            asyncio.run(send_command_to_agent(device_key, "execute_cmd", {"command": cmd}, user_id=user["id"]))
    finally:
        devices.pop(device_key, None)


def test_execute_cmd_allows_read_only_probe_of_other_windows_user_profile_path(client):
    from server.database import create_user, upsert_device_profile
    import server.task_runtime as task_runtime

    user = create_user("path-guard-probe-user")
    device_key = f"{user['id']}:device-1"
    info = {"hostname": "newbox", "os": "Windows", "username": "User", "home": r"C:\Users\User"}
    upsert_device_profile("device-1", user["id"], {**info, "desktop_path": r"C:\Users\User\Desktop", "machine_guid": "machine-1"})

    class FakeWS:
        async def send_text(self, _msg):
            pending = next(iter(task_runtime.devices[device_key]["pending"].values()))
            pending.set_result({"returncode": 0, "stdout": "False", "stderr": ""})

    task_runtime.devices[device_key] = {"user_id": user["id"], "info": info, "pending": {}, "ws": FakeWS()}

    try:
        result = asyncio.run(
            task_runtime.send_command_to_agent(
                device_key,
                "execute_cmd",
                {"command": r'Test-Path "C:\Users\russa\TEST_IRU"'},
                user_id=user["id"],
            )
        )
    finally:
        task_runtime.devices.pop(device_key, None)

    assert result["stdout"] == "False"


def test_relative_preference_can_resolve_under_current_home():
    assert resolve_relative_preference("TEST_IRU", {"home": r"C:\Users\User"}, {"username": "User"}) == r"C:\Users\User\TEST_IRU"


def test_device_memory_path_with_matching_machine_guid_is_allowed_in_memory_block(client):
    from server.database import add_fact

    user = _create_user("path-scope-device-memory")
    add_fact("machine-new", "device-new", r"Project path is C:\Users\User\TEST_IRU", "config")

    block = build_memory_block("machine-new", str(user["id"]))

    assert r"C:\Users\User\TEST_IRU" in block


def test_device_memory_path_with_different_machine_guid_is_not_exposed(client):
    from server.database import add_fact

    user = _create_user("path-scope-other-device-memory")
    add_fact("machine-old", "device-old", r"Old path is C:\Users\russa\TEST_IRU", "config")

    block = build_memory_block("machine-new", str(user["id"]))

    assert r"C:\Users\russa\TEST_IRU" not in block


def test_unknown_provenance_absolute_path_fact_is_filtered():
    facts = [{"id": 1, "text": r"Old path C:\Users\russa\TEST_IRU", "category": "config"}]

    assert filter_memory_facts_for_device(facts) == []


def test_prompt_rules_mark_absolute_paths_as_device_scoped():
    assert "Absolute paths are device-scoped" in SYSTEM_PROMPT_TEMPLATE
    assert "Сохранённый путь из памяти — это подсказка" in SYSTEM_PROMPT_TEMPLATE
    assert "Never create missing C:\\Users\\<name> profile folders" in SYSTEM_PROMPT_TEMPLATE

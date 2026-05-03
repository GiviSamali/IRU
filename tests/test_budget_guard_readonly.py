"""
Regression tests for the read-only inspection fix in controller_budget.py.

Covers:
1. Normal inspection sequence (GCI → GC full → GC -Tail) must NOT trigger budget_guard.
2. Repeated Start-Process calc variants MUST still trigger budget_guard.
3. Different Get-Content paths are NOT the same retry spiral.
4. Identical command repeated many times is still blocked.
5. Pipeline worker and direct CommandBudget use the same behaviour (shared helper).
"""
import asyncio
import json

import httpx
import pytest

from server.controller_budget import (
    CommandBudget,
    normalize_execute_cmd,
    _is_readonly_cmd,
    MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK,
    MAX_SIMILAR_READONLY_CALLS_PER_TASK,
)
from server.controller_pipeline import run_pipeline_worker


# ---------------------------------------------------------------------------
# Unit tests — normalize_execute_cmd
# ---------------------------------------------------------------------------

class TestNormalizeExecuteCmd:
    def test_get_childitem_key_contains_path(self):
        key = normalize_execute_cmd(
            r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse -Include "*.html"'
        )
        assert key.startswith("get-childitem")
        assert "test_iru" in key or "c:" in key

    def test_get_content_full_vs_tail_differ(self):
        full_key = normalize_execute_cmd(
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8'
        )
        tail_key = normalize_execute_cmd(
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8 -Tail 200'
        )
        assert full_key != tail_key
        assert "-tail" in tail_key
        assert "-tail" not in full_key

    def test_get_childitem_vs_get_content_differ(self):
        gci_key = normalize_execute_cmd(
            r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse'
        )
        gc_key = normalize_execute_cmd(
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html"'
        )
        assert gci_key != gc_key
        assert gci_key.startswith("get-childitem")
        assert gc_key.startswith("get-content")

    def test_different_paths_produce_different_keys(self):
        key_a = normalize_execute_cmd(r'Get-Content -Path "C:\file_a.html"')
        key_b = normalize_execute_cmd(r'Get-Content -Path "C:\file_b.html"')
        assert key_a != key_b

    def test_start_process_variants_collapse_to_same_key(self):
        keys = [
            normalize_execute_cmd("Start-Process calc.exe"),
            normalize_execute_cmd('Start-Process -FilePath "calc.exe"'),
            normalize_execute_cmd("Start-Process calc"),
            normalize_execute_cmd("Start-Process -FilePath calc.exe"),
        ]
        assert len(set(keys)) == 1, f"Expected one key, got: {set(keys)}"


# ---------------------------------------------------------------------------
# Unit tests — _is_readonly_cmd
# ---------------------------------------------------------------------------

class TestIsReadonlyCmd:
    @pytest.mark.parametrize("cmd", [
        "get-childitem c:\\dir",
        "get-content c:\\file.txt",
        "get-content c:\\file.txt -tail",
        "test-path c:\\somewhere",
        "resolve-path .",
        "select-string pattern",
        "get-process explorer",
        "get-service wuauserv",
        "dir .",
        "ls /tmp",
        "cat /etc/hosts",
        "type nul",
        "grep pattern",
        "findstr /s pattern",
    ])
    def test_readonly_cmds_are_detected(self, cmd):
        assert _is_readonly_cmd(cmd), f"{cmd!r} should be read-only"

    @pytest.mark.parametrize("cmd", [
        "start-process calc",
        "invoke-expression code",
        "remove-item c:\\file",
        "whoami",
    ])
    def test_non_readonly_cmds_are_not_detected(self, cmd):
        assert not _is_readonly_cmd(cmd), f"{cmd!r} should NOT be read-only"


# ---------------------------------------------------------------------------
# Unit tests — CommandBudget behaviour
# ---------------------------------------------------------------------------

class TestCommandBudget:
    """Tests that use CommandBudget directly (shared by pipeline AND non-pipeline)."""

    def test_inspection_sequence_is_not_blocked(self):
        """
        Requirement 1: GCI + GC full + GC -Tail on the same file must not trigger budget_guard.
        """
        budget = CommandBudget(
            max_tool_calls=20,
            max_execute_cmd_calls=20,
        )
        cmds = [
            r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse -Include "*.html", "*.htm", "*.css"',
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8',
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8 -Tail 200',
        ]
        for cmd in cmds:
            result = budget.register("execute_cmd", cmd)
            assert result is None, (
                f"budget_guard should NOT have fired for: {cmd!r}, got: {result!r}"
            )

    def test_start_process_retry_spiral_is_blocked(self):
        """
        Requirement 2: Repeated Start-Process calc variants must still trigger budget_guard.
        """
        budget = CommandBudget(
            max_tool_calls=20,
            max_execute_cmd_calls=20,
        )
        cmds = [
            "Start-Process calc.exe",
            'Start-Process -FilePath "calc.exe"',
            "Start-Process calc",
            "Start-Process -FilePath calc.exe",  # 4th — should be blocked (limit=3)
        ]
        results = [budget.register("execute_cmd", cmd) for cmd in cmds]
        assert results[:3] == [None, None, None], "First 3 must pass"
        assert results[3] is not None, "4th variant of Start-Process calc must trigger budget_guard"

    def test_different_paths_not_same_spiral(self):
        """
        Requirement 3: Get-Content on different paths must NOT be counted together.
        """
        budget = CommandBudget(
            max_tool_calls=20,
            max_execute_cmd_calls=20,
        )
        paths = [
            r"C:\Users\russa\TEST_IRU\file_a.html",
            r"C:\Users\russa\TEST_IRU\file_b.html",
            r"C:\Users\russa\TEST_IRU\file_c.html",
            r"C:\Users\russa\TEST_IRU\file_d.html",
        ]
        for path in paths:
            cmd = f'Get-Content -Path "{path}" -Encoding UTF8'
            result = budget.register("execute_cmd", cmd)
            assert result is None, f"Different path should not trigger budget_guard: {path!r}"

    def test_identical_command_repeated_is_blocked(self):
        """
        Requirement 4: Same exact command repeated more than readonly limit must be blocked.
        """
        budget = CommandBudget(
            max_tool_calls=100,
            max_execute_cmd_calls=100,
            max_similar_readonly_calls=MAX_SIMILAR_READONLY_CALLS_PER_TASK,
        )
        cmd = r'Get-Content -Path "C:\file.html" -Encoding UTF8'
        # First N calls must pass
        for _ in range(MAX_SIMILAR_READONLY_CALLS_PER_TASK):
            result = budget.register("execute_cmd", cmd)
            assert result is None
        # Next call must be blocked
        result = budget.register("execute_cmd", cmd)
        assert result is not None, "Repeating the same readonly cmd beyond limit must be blocked"

    def test_non_readonly_limit_is_stricter(self):
        """
        Non-readonly commands use the original (stricter) limit.
        """
        assert MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK < MAX_SIMILAR_READONLY_CALLS_PER_TASK, (
            "readonly limit must be more permissive than the regular limit"
        )

    def test_max_execute_cmd_calls_still_enforced(self):
        """
        Requirement 4 (no-disable): max_execute_cmd_calls_per_task is still respected.
        """
        budget = CommandBudget(
            max_tool_calls=100,
            max_execute_cmd_calls=3,
            max_similar_readonly_calls=100,
        )
        # Three different readonly cmds — no spiral, but execute_cmd cap fires
        paths = [r"C:\a.html", r"C:\b.html", r"C:\c.html", r"C:\d.html"]
        results = [
            budget.register("execute_cmd", f'Get-Content -Path "{p}"') for p in paths
        ]
        assert results[:3] == [None, None, None]
        assert results[3] is not None, "max_execute_cmd_calls must still be respected"

    def test_max_tool_calls_still_enforced(self):
        """
        Requirement 4 (no-disable): max_tool_calls_per_task is still respected.
        """
        budget = CommandBudget(max_tool_calls=3, max_execute_cmd_calls=100)
        budget.register("read_memory", "")
        budget.register("read_memory", "")
        budget.register("read_memory", "")
        result = budget.register("read_memory", "")
        assert result is not None, "max_tool_calls must still be respected"


# ---------------------------------------------------------------------------
# Integration tests — pipeline worker (Requirement 5)
# ---------------------------------------------------------------------------

def _make_completion_fn(responses):
    queue = list(responses)

    async def _fn(**kwargs):
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _fn


def _execute_call(call_id: str, command: str):
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def _shared_context():
    return {
        "current_device_id": "device-1",
        "current_hostname": "devbox",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "other_devices_summary": "",
        "target_device_id": "device-1",
        "os_rules": "",
        "current_datetime_msk": "2026-05-03 10:00",
    }


def _run_worker(responses, send_command_fn=None):
    async def _noop(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=_shared_context(),
                overall_goal="goal",
                step={"title": "step", "instruction": "inspect files", "device_id": "device-1"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=send_command_fn or _noop,
                get_file_link_fn=lambda *a: "/api/download/mock",
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_make_completion_fn(responses),
                worker_tools=[],
            )

    return asyncio.run(_run())


def test_pipeline_inspection_sequence_not_blocked():
    """
    Requirement 5: Pipeline worker must NOT trigger budget_guard for the
    canonical inspection sequence (GCI + GC full + GC -Tail).
    """
    inspection_calls = [
        _execute_call(
            "c1",
            r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse -Include "*.html", "*.htm", "*.css"',
        ),
        _execute_call(
            "c2",
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8',
        ),
        _execute_call(
            "c3",
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8 -Tail 200',
        ),
    ]
    executed = []

    async def _send(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "content", "stderr": ""}

    result = _run_worker(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": inspection_calls},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "inspection done"},
                }]
            },
        ],
        send_command_fn=_send,
    )

    assert len(executed) == 3, f"All 3 inspection commands must execute, got: {executed}"
    # No budget_guard action in commands list
    actions = [c.get("action") for c in result.get("commands", [])]
    assert "budget_guard" not in actions, f"budget_guard must not fire; commands={result.get('commands')}"
    assert result["status"] == "ok"


def test_pipeline_start_process_spiral_still_blocked():
    """
    Requirement 5: Pipeline worker must still block Start-Process calc retry spiral.
    """
    spiral_calls = [
        _execute_call("c1", "Start-Process calc.exe"),
        _execute_call("c2", 'Start-Process -FilePath "calc.exe"'),
        _execute_call("c3", "Start-Process calc"),
        _execute_call("c4", "Start-Process -FilePath calc.exe"),
    ]
    executed = []

    async def _send(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker(
        responses=[{
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": spiral_calls},
            }]
        }],
        send_command_fn=_send,
    )

    assert len(executed) == 3, f"Only 3 Start-Process calls should execute, got {len(executed)}"
    assert result["commands"][-1]["action"] == "budget_guard"

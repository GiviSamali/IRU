"""
Regression tests for controller_budget.py — command classification system.

Covers all 7 requirements:
1. Python env-discovery sequence (10-12 cmds) does NOT trigger budget_guard.
2. Read-only project inspection sequence does NOT trigger budget_guard.
3. Repeated Start-Process calc variants STILL trigger budget_guard.
4. Repeated identical unknown command STILL triggers budget_guard.
5. pip install classified as package_install_or_setup (mutating), not read-only.
6. python -m pip --version classified as environment_discovery.
7. CommandBudget used identically in non-pipeline and pipeline tests.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.controller_budget import (
    CmdCategory,
    CommandBudget,
    MAX_SIMILAR_ENVIRONMENT_DISCOVERY,
    MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK,
    MAX_SIMILAR_READ_ONLY,
    MAX_SIMILAR_READONLY_CALLS_PER_TASK,  # legacy alias
    classify_cmd,
    normalize_execute_cmd,
    _is_readonly_cmd,
)
from server.controller_pipeline import run_pipeline_worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _budget(
    *,
    max_tool_calls: int = 200,
    max_execute_cmd_calls: int = 200,
    max_mutating_cmd_calls: int = 200,
    max_environment_discovery_calls: int = 200,
    max_read_only_cmd_calls: int = 200,
    max_similar_execute_cmd_calls: int = MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK,
    max_similar_readonly_calls: int = MAX_SIMILAR_READ_ONLY,
    max_similar_environment_discovery: int = MAX_SIMILAR_ENVIRONMENT_DISCOVERY,
    max_repeated_failed_result: int = 3,
) -> CommandBudget:
    """Create a CommandBudget with generous caps and only the specified limits active."""
    return CommandBudget(
        max_tool_calls=max_tool_calls,
        max_execute_cmd_calls=max_execute_cmd_calls,
        max_mutating_cmd_calls=max_mutating_cmd_calls,
        max_environment_discovery_calls=max_environment_discovery_calls,
        max_read_only_cmd_calls=max_read_only_cmd_calls,
        max_similar_execute_cmd_calls=max_similar_execute_cmd_calls,
        max_similar_readonly_calls=max_similar_readonly_calls,
        max_similar_environment_discovery=max_similar_environment_discovery,
        max_repeated_failed_result=max_repeated_failed_result,
    )


# ---------------------------------------------------------------------------
# 1. classify_cmd
# ---------------------------------------------------------------------------

class TestClassifyCmd:
    @pytest.mark.parametrize("cmd,expected", [
        # environment_discovery
        ("python --version",                          CmdCategory.ENVIRONMENT_DISCOVERY),
        ("python -V",                                 CmdCategory.ENVIRONMENT_DISCOVERY),
        ("py --version",                              CmdCategory.ENVIRONMENT_DISCOVERY),
        ("py -V",                                     CmdCategory.ENVIRONMENT_DISCOVERY),
        ("python3 --version",                         CmdCategory.ENVIRONMENT_DISCOVERY),
        ("python -m pip --version",                   CmdCategory.ENVIRONMENT_DISCOVERY),
        ("py -m pip --version",                       CmdCategory.ENVIRONMENT_DISCOVERY),
        ("pip --version",                             CmdCategory.ENVIRONMENT_DISCOVERY),
        ("pip3 --version",                            CmdCategory.ENVIRONMENT_DISCOVERY),
        ("where python",                              CmdCategory.ENVIRONMENT_DISCOVERY),
        ("where py",                                  CmdCategory.ENVIRONMENT_DISCOVERY),
        ("Get-Command python",                        CmdCategory.ENVIRONMENT_DISCOVERY),
        ("Get-Command py",                            CmdCategory.ENVIRONMENT_DISCOVERY),
        ('python -c "import sys; print(sys.executable)"', CmdCategory.ENVIRONMENT_DISCOVERY),
        ('python -c "import site; print(site.getsitepackages())"', CmdCategory.ENVIRONMENT_DISCOVERY),
        ('py -c "import sys; print(sys.version)"',   CmdCategory.ENVIRONMENT_DISCOVERY),
        # read_only_inspection
        (r'Get-ChildItem -Path "C:\Users" -Recurse',  CmdCategory.READ_ONLY_INSPECTION),
        (r'Get-Content -Path "C:\file.txt"',          CmdCategory.READ_ONLY_INSPECTION),
        (r'Get-Content -Path "C:\file.txt" -Tail 200', CmdCategory.READ_ONLY_INSPECTION),
        ("Test-Path C:\\dir",                         CmdCategory.READ_ONLY_INSPECTION),
        ("Resolve-Path .",                            CmdCategory.READ_ONLY_INSPECTION),
        ("Select-String -Pattern foo -Path *.py",     CmdCategory.READ_ONLY_INSPECTION),
        ("dir C:\\",                                  CmdCategory.READ_ONLY_INSPECTION),
        ("ls /tmp",                                   CmdCategory.READ_ONLY_INSPECTION),
        ("cat /etc/hosts",                            CmdCategory.READ_ONLY_INSPECTION),
        ("grep pattern file.txt",                     CmdCategory.READ_ONLY_INSPECTION),
        # package_install_or_setup
        ("pip install PyQt5",                         CmdCategory.PACKAGE_INSTALL),
        ("pip3 install requests",                     CmdCategory.PACKAGE_INSTALL),
        ("python -m pip install numpy",               CmdCategory.PACKAGE_INSTALL),
        ("py -m pip install flask",                   CmdCategory.PACKAGE_INSTALL),
        # process_launch
        ("Start-Process calc.exe",                    CmdCategory.PROCESS_LAUNCH),
        ('Start-Process -FilePath "notepad.exe"',     CmdCategory.PROCESS_LAUNCH),
        # destructive
        ("Remove-Item C:\\file.txt",                  CmdCategory.DESTRUCTIVE),
        ("del C:\\file.txt",                          CmdCategory.DESTRUCTIVE),
    ])
    def test_classification(self, cmd: str, expected: CmdCategory):
        result = classify_cmd(cmd)
        assert result == expected, (
            f"classify_cmd({cmd!r}) = {result!r}, expected {expected!r}"
        )

    def test_pip_install_is_not_read_only(self):
        """Requirement 5: pip install must be PACKAGE_INSTALL, not read-only."""
        cat = classify_cmd("pip install PyQt5")
        assert cat == CmdCategory.PACKAGE_INSTALL
        assert cat != CmdCategory.READ_ONLY_INSPECTION

    def test_pip_version_is_env_discovery_not_setup(self):
        """Requirement 6: python -m pip --version must be ENVIRONMENT_DISCOVERY."""
        cat = classify_cmd("python -m pip --version")
        assert cat == CmdCategory.ENVIRONMENT_DISCOVERY
        assert cat != CmdCategory.PACKAGE_INSTALL


# ---------------------------------------------------------------------------
# 2. normalize_execute_cmd
# ---------------------------------------------------------------------------

class TestNormalizeExecuteCmd:
    def test_get_content_full_vs_tail_differ(self):
        full = normalize_execute_cmd(r'Get-Content -Path "C:\f.html" -Encoding UTF8')
        tail = normalize_execute_cmd(r'Get-Content -Path "C:\f.html" -Encoding UTF8 -Tail 200')
        assert full != tail
        assert "-tail" in tail and "-tail" not in full

    def test_get_childitem_vs_get_content_differ(self):
        gci = normalize_execute_cmd(r'Get-ChildItem -Path "C:\dir"')
        gc  = normalize_execute_cmd(r'Get-Content -Path "C:\dir\f.txt"')
        assert gci.startswith("get-childitem")
        assert gc.startswith("get-content")
        assert gci != gc

    def test_different_paths_produce_different_keys(self):
        assert normalize_execute_cmd(r'Get-Content -Path "C:\a.txt"') != \
               normalize_execute_cmd(r'Get-Content -Path "C:\b.txt"')

    def test_start_process_variants_collapse(self):
        keys = [
            normalize_execute_cmd("Start-Process calc.exe"),
            normalize_execute_cmd('Start-Process -FilePath "calc.exe"'),
            normalize_execute_cmd("Start-Process calc"),
        ]
        assert len(set(keys)) == 1, f"Expected one key, got: {set(keys)}"

    def test_python_version_variants_collapse(self):
        """python / python3 / py --version must share one key."""
        keys = [
            normalize_execute_cmd("python --version"),
            normalize_execute_cmd("python3 --version"),
            normalize_execute_cmd("py --version"),
        ]
        assert len(set(keys)) == 1, f"Expected one key, got: {set(keys)}"

    def test_pip_version_vs_install_differ(self):
        assert normalize_execute_cmd("pip --version") != normalize_execute_cmd("pip install PyQt5")

    def test_python_m_pip_version_vs_install_differ(self):
        v = normalize_execute_cmd("python -m pip --version")
        i = normalize_execute_cmd("python -m pip install PyQt5")
        assert v != i

    def test_where_python_variants_collapse(self):
        keys = [
            normalize_execute_cmd("where python"),
            normalize_execute_cmd("where py"),
            normalize_execute_cmd("where python3"),
        ]
        assert len(set(keys)) == 1, f"Expected one key, got: {set(keys)}"


# ---------------------------------------------------------------------------
# 3. CommandBudget unit tests
# ---------------------------------------------------------------------------

class TestCommandBudget:

    # ── Requirement 1: environment discovery sequence ──────────────────────
    def test_env_discovery_sequence_not_blocked(self):
        """
        12 distinct environment-discovery commands must all pass.
        None of them should consume the mutating budget.
        """
        budget = _budget(max_mutating_cmd_calls=3)  # tight mutating cap — must not fire
        cmds = [
            "python --version",
            "py --version",
            "where python",
            "where py",
            "Get-Command python",
            "Get-Command py",
            "python -m pip --version",
            "py -m pip --version",
            "pip --version",
            'python -c "import sys; print(sys.executable)"',
            'python -c "import site; print(site.getsitepackages())"',
            'py -c "import sys; print(sys.version)"',
        ]
        for cmd in cmds:
            result = budget.register("execute_cmd", cmd)
            assert result is None, (
                f"budget_guard fired unexpectedly on env-discovery: {cmd!r}\n"
                f"  category={classify_cmd(cmd)}, key={normalize_execute_cmd(cmd)!r}"
            )
        # Mutating budget must remain untouched
        assert budget.mutating_cmd_count == 0

    # ── Requirement 2: read-only inspection sequence ──────────────────────
    def test_readonly_inspection_sequence_not_blocked(self):
        budget = _budget(max_mutating_cmd_calls=2)  # tight mutating cap — must not fire
        cmds = [
            r'Test-Path "C:\Users\russa\TEST_IRU"',
            r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse -Include "*.html"',
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8',
            r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8 -Tail 200',
            r'Select-String -Pattern "<title>" -Path "C:\Users\russa\TEST_IRU\iru_website.html"',
        ]
        for cmd in cmds:
            result = budget.register("execute_cmd", cmd)
            assert result is None, f"budget_guard fired on read-only: {cmd!r}"
        assert budget.mutating_cmd_count == 0

    # ── Requirement 3: Start-Process spiral blocked ───────────────────────
    def test_start_process_retry_spiral_blocked(self):
        budget = _budget()
        cmds = [
            "Start-Process calc.exe",
            'Start-Process -FilePath "calc.exe"',
            "Start-Process calc",
            "Start-Process -FilePath calc.exe",   # 4th — blocked (limit=3)
        ]
        results = [budget.register("execute_cmd", c) for c in cmds]
        assert results[:3] == [None, None, None]
        assert results[3] is not None, "4th Start-Process calc must trigger budget_guard"

    # ── Requirement 4: identical unknown command blocked ─────────────────
    def test_identical_unknown_cmd_blocked(self):
        budget = _budget(max_similar_execute_cmd_calls=3)
        cmd = "whoami"
        for _ in range(3):
            assert budget.register("execute_cmd", cmd) is None
        assert budget.register("execute_cmd", cmd) is not None

    def test_normal_artifact_creation_sequence_not_blocked(self):
        budget = CommandBudget()
        calls = [
            ("execute_cmd", 'python -c "import PyQt5"'),
            ("write_content", "questions.json"),
            ("execute_cmd", r'Get-Content ".\questions.json" | ConvertFrom-Json | Out-Null'),
            ("write_content", "test_db.py"),
            ("execute_cmd", "python -m py_compile test_db.py"),
        ]

        for fn, command in calls:
            assert budget.register(fn, command) is None

    def test_multiple_write_content_calls_do_not_block(self):
        budget = CommandBudget()
        for idx in range(20):
            assert budget.register("write_content", f"file_{idx}.txt") is None
        for _ in range(8):
            assert budget.register("write_content", "large_file.txt append=true") is None

    def test_distinct_mutating_commands_are_not_blocked_by_ordinary_count(self):
        budget = CommandBudget()
        cmds = [
            "pip install PyQt5",
            "python -m pip install requests",
            r'New-Item -ItemType Directory "C:\tmp\iru_a"',
            r'Set-Content -Path "C:\tmp\iru_a\a.txt" -Value "a"',
            r'Copy-Item "C:\tmp\iru_a\a.txt" "C:\tmp\iru_b.txt"',
        ]
        for cmd in cmds:
            assert budget.register("execute_cmd", cmd) is None

    # ── Requirement 6: python -m pip --version is env-discovery ─────────
    def test_pip_version_does_not_consume_mutating_budget(self):
        budget = _budget(max_mutating_cmd_calls=1)  # only 1 mutating allowed
        for cmd in [
            "python -m pip --version",
            "py -m pip --version",
            "pip --version",
            "pip3 --version",
        ]:
            result = budget.register("execute_cmd", cmd)
            assert result is None, f"{cmd!r} must NOT consume mutating budget"
        assert budget.mutating_cmd_count == 0

    def test_repeated_destructive_same_target_blocked(self):
        budget = _budget()
        cmds = [
            r'Remove-Item -LiteralPath "C:\tmp\same.txt"',
            r'del "C:\tmp\same.txt"',
            r'rd "C:\tmp\same.txt"',
        ]
        results = [budget.register("execute_cmd", cmd) for cmd in cmds]
        assert results[:2] == [None, None]
        assert results[2] is not None

    def test_same_failed_command_error_is_blocked(self):
        budget = _budget(max_similar_execute_cmd_calls=99, max_repeated_failed_result=2)
        cmd = "python run_task.py"
        result = {"returncode": 1, "stdout": "", "stderr": "same deterministic failure"}

        for _ in range(2):
            assert budget.register("execute_cmd", cmd) is None
            assert budget.observe_execute_result(cmd, result) is None
        assert budget.register("execute_cmd", cmd) is None
        assert budget.observe_execute_result(cmd, result) is not None

    # ── Hard caps still enforced ────────────────────────────────────
    def test_max_tool_calls_still_enforced(self):
        budget = CommandBudget(max_tool_calls=3, max_execute_cmd_calls=999,
                               max_mutating_cmd_calls=999)
        for _ in range(3):
            budget.register("read_memory", "")
        assert budget.register("read_memory", "") is not None

    def test_max_env_discovery_hard_cap(self):
        budget = _budget(max_environment_discovery_calls=3)
        for _ in range(3):
            assert budget.register("execute_cmd", "python --version") is None
        assert budget.register("execute_cmd", "python --version") is not None

    def test_max_read_only_hard_cap(self):
        budget = _budget(max_read_only_cmd_calls=2)
        assert budget.register("execute_cmd", r'Get-Content -Path "C:\a.txt"') is None
        assert budget.register("execute_cmd", r'Get-Content -Path "C:\b.txt"') is None
        assert budget.register("execute_cmd", r'Get-Content -Path "C:\c.txt"') is not None

    def test_similar_readonly_limit(self):
        budget = _budget(max_similar_readonly_calls=MAX_SIMILAR_READ_ONLY)
        cmd = r'Get-Content -Path "C:\same.txt"'
        for _ in range(MAX_SIMILAR_READ_ONLY):
            assert budget.register("execute_cmd", cmd) is None
        assert budget.register("execute_cmd", cmd) is not None

    def test_different_paths_are_separate_keys(self):
        budget = _budget()
        paths = [r"C:\a.html", r"C:\b.html", r"C:\c.html", r"C:\d.html"]
        for p in paths:
            result = budget.register("execute_cmd", f'Get-Content -Path "{p}"')
            assert result is None, f"Different path should not trigger guard: {p!r}"


# ---------------------------------------------------------------------------
# 4. Pipeline integration tests (Requirement 7)
# ---------------------------------------------------------------------------

def _make_completion_fn(responses: list):
    queue = list(responses)

    async def _fn(**kwargs):
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _fn


def _exec_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def _write_call(call_id: str, path: str, append: bool = False) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "write_content",
            "arguments": json.dumps({"path": path, "content": "data", "append": append}),
        },
    }


def _shared_ctx() -> dict:
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


def _run_worker(responses: list, send_fn=None) -> dict:
    async def _noop(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=_shared_ctx(),
                overall_goal="goal",
                step={"title": "step", "instruction": "inspect", "device_id": "device-1"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=send_fn or _noop,
                get_file_link_fn=lambda *a: "/api/download/mock",
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_make_completion_fn(responses),
                worker_tools=[],
            )

    return asyncio.run(_run())


def test_pipeline_env_discovery_sequence_not_blocked():
    """
    Req 7: Pipeline must NOT block the full env-discovery sequence.
    """
    discovery_calls = [
        _exec_call("c1",  "python --version"),
        _exec_call("c2",  "py --version"),
        _exec_call("c3",  "where python"),
        _exec_call("c4",  "where py"),
        _exec_call("c5",  "Get-Command python"),
        _exec_call("c6",  "python -m pip --version"),
        _exec_call("c7",  "pip --version"),
        _exec_call("c8",  'python -c "import sys; print(sys.executable)"'),
        _exec_call("c9",  'python -c "import site; print(site.getsitepackages())"'),
    ]
    executed: list[str] = []

    async def _send(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": discovery_calls},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "env found"},
                }]
            },
        ],
        send_fn=_send,
    )

    assert len(executed) == len(discovery_calls), (
        f"Expected {len(discovery_calls)} commands to run, got {len(executed)}:\n"
        + "\n".join(f"  {c}" for c in executed)
    )
    actions = [c.get("action") for c in result.get("commands", [])]
    assert "budget_guard" not in actions
    assert result["status"] == "ok"


def test_pipeline_readonly_inspection_not_blocked():
    """
    Req 7: Pipeline must NOT block the canonical read-only inspection sequence.
    """
    inspection_calls = [
        _exec_call("c0", r'Test-Path "C:\Users\russa\TEST_IRU"'),
        _exec_call("c1", r'Get-ChildItem -Path "C:\Users\russa\TEST_IRU" -Recurse -Include "*.html"'),
        _exec_call("c2", r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8'),
        _exec_call("c3", r'Get-Content -Path "C:\Users\russa\TEST_IRU\iru_website.html" -Encoding UTF8 -Tail 200'),
        _exec_call("c4", r'Select-String -Pattern "<title>" -Path "C:\Users\russa\TEST_IRU\iru_website.html"'),
    ]
    executed: list[str] = []

    async def _send(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "data", "stderr": ""}

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
        send_fn=_send,
    )

    assert len(executed) == 5
    assert "budget_guard" not in [c.get("action") for c in result.get("commands", [])]
    assert result["status"] == "ok"


def test_pipeline_artifact_creation_sequence_not_blocked():
    calls = [
        _exec_call("c1", 'python -c "import PyQt5"'),
        _write_call("c2", r"C:\work\questions.json"),
        _exec_call("c3", r'Get-Content "C:\work\questions.json" | ConvertFrom-Json | Out-Null'),
        _write_call("c4", r"C:\work\test_db.py"),
        _exec_call("c5", r"python -m py_compile C:\work\test_db.py"),
    ]
    executed: list[tuple[str, str]] = []

    async def _send(device_id, action, params):
        executed.append((action, params.get("command") or params.get("path")))
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": calls},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "artifact created"},
                }]
            },
        ],
        send_fn=_send,
    )

    assert len(executed) == len(calls)
    assert "budget_guard" not in [c.get("action") for c in result.get("commands", [])]
    assert result["status"] == "ok"


def test_pipeline_start_process_spiral_still_blocked():
    """
    Req 7: Pipeline must still block Start-Process calc retry spiral.
    """
    spiral_calls = [
        _exec_call("c1", "Start-Process calc.exe"),
        _exec_call("c2", 'Start-Process -FilePath "calc.exe"'),
        _exec_call("c3", "Start-Process calc"),
        _exec_call("c4", "Start-Process -FilePath calc.exe"),
    ]
    executed: list[str] = []

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
        send_fn=_send,
    )

    assert len(executed) == 3
    assert result["commands"][-1]["action"] == "budget_guard"

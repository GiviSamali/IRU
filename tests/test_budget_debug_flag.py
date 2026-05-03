"""
tests/test_budget_debug_flag.py

Verifies that:
- IRU_DEBUG_BUDGET=1 enables debug logging but does NOT change register() results.
- IRU_DEBUG_BUDGET off (default) produces no [budget] log records.
- Debug flag does not alter budget block decisions in any scenario.
"""
from __future__ import annotations

import logging
import os

import pytest

from server.controller_budget import CommandBudget, BUDGET_GUARD_ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_budget(**kwargs) -> CommandBudget:
    """Budget with very generous caps so only spiral limits matter."""
    defaults = dict(
        max_tool_calls=200,
        max_execute_cmd_calls=200,
        max_mutating_cmd_calls=200,
        max_environment_discovery_calls=200,
        max_read_only_cmd_calls=200,
    )
    defaults.update(kwargs)
    return CommandBudget(**defaults)


# ---------------------------------------------------------------------------
# 1. Debug flag off — no log records emitted
# ---------------------------------------------------------------------------

def test_debug_flag_off_no_log_records(caplog, monkeypatch):
    monkeypatch.delenv("IRU_DEBUG_BUDGET", raising=False)
    budget = _make_budget()

    with caplog.at_level(logging.DEBUG, logger="iru.budget"):
        budget.register("execute_cmd", "python --version")
        budget.register("execute_cmd", "Get-ChildItem C:\\")
        budget.register("read_memory", "")

    budget_records = [r for r in caplog.records if r.name == "iru.budget"]
    assert budget_records == [], (
        f"Expected no log records when IRU_DEBUG_BUDGET is off, got: {budget_records}"
    )


# ---------------------------------------------------------------------------
# 2. Debug flag on — log records ARE emitted
# ---------------------------------------------------------------------------

def test_debug_flag_on_emits_log_records(caplog, monkeypatch):
    monkeypatch.setenv("IRU_DEBUG_BUDGET", "1")
    budget = _make_budget()

    with caplog.at_level(logging.DEBUG, logger="iru.budget"):
        budget.register("execute_cmd", "python --version")
        budget.register("execute_cmd", "pip install PyQt5")

    budget_records = [r for r in caplog.records if r.name == "iru.budget"]
    assert len(budget_records) >= 2, (
        f"Expected at least 2 log records with IRU_DEBUG_BUDGET=1, got: {len(budget_records)}"
    )


# ---------------------------------------------------------------------------
# 3. Core invariant: register() result is identical regardless of debug flag
# ---------------------------------------------------------------------------

_TEST_COMMANDS = [
    ("execute_cmd", "python --version"),
    ("execute_cmd", "py --version"),
    ("execute_cmd", "where python"),
    ("execute_cmd", "Get-Command python"),
    ("execute_cmd", "python -m pip --version"),
    ("execute_cmd", "pip --version"),
    ("execute_cmd", 'python -c "import sys; print(sys.executable)"'),
    ("execute_cmd", r'Get-ChildItem -Path "C:\Users" -Recurse'),
    ("execute_cmd", r'Get-Content -Path "C:\file.html"'),
    ("execute_cmd", r'Get-Content -Path "C:\file.html" -Tail 200'),
    ("execute_cmd", "Start-Process calc.exe"),
    ("execute_cmd", 'Start-Process -FilePath "calc.exe"'),
    ("execute_cmd", "Start-Process calc"),
    # 4th Start-Process — should be blocked
    ("execute_cmd", "Start-Process -FilePath calc.exe"),
    ("read_memory", ""),
]


def _collect_results(debug_value: str | None) -> list[str | None]:
    """Run _TEST_COMMANDS through a fresh budget with the given env-var value."""
    env = os.environ.copy()
    if debug_value is None:
        env.pop("IRU_DEBUG_BUDGET", None)
    else:
        env["IRU_DEBUG_BUDGET"] = debug_value

    # Temporarily patch the env so _debug_enabled() picks it up.
    original = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        budget = _make_budget()
        return [budget.register(fn, cmd) for fn, cmd in _TEST_COMMANDS]
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_debug_flag_does_not_change_register_results():
    """
    Requirement 8: debug flag must not alter budget_guard block decisions.
    Results with IRU_DEBUG_BUDGET=1 must exactly match results without it.
    """
    results_off = _collect_results(None)
    results_on  = _collect_results("1")

    assert results_off == results_on, (
        "register() results differ between debug-off and debug-on:\n"
        + "\n".join(
            f"  [{i}] ({fn!r}, {cmd[:60]!r}): off={off!r} on={on!r}"
            for i, ((fn, cmd), off, on) in enumerate(
                zip(_TEST_COMMANDS, results_off, results_on)
            )
            if off != on
        )
    )


# ---------------------------------------------------------------------------
# 4. Debug log contains expected fields when flag is on
# ---------------------------------------------------------------------------

def test_debug_log_contains_required_fields(caplog, monkeypatch):
    """
    When IRU_DEBUG_BUDGET=1, each log record for execute_cmd must include:
    fn=, key=, category=, tool_calls=, same_key=, block_reason=
    """
    monkeypatch.setenv("IRU_DEBUG_BUDGET", "1")
    budget = _make_budget()

    with caplog.at_level(logging.DEBUG, logger="iru.budget"):
        budget.register("execute_cmd", "python --version")

    exec_records = [
        r for r in caplog.records
        if r.name == "iru.budget" and "execute_cmd" in r.getMessage()
    ]
    assert exec_records, "No execute_cmd log record found"

    msg = exec_records[0].getMessage()
    for field in ("fn=execute_cmd", "key=", "category=", "tool_calls=", "same_key=", "block_reason="):
        assert field in msg, f"Expected field {field!r} in log message:\n{msg}"


# ---------------------------------------------------------------------------
# 5. Block log record present when budget is exceeded
# ---------------------------------------------------------------------------

def test_debug_log_block_record_on_spiral(caplog, monkeypatch):
    monkeypatch.setenv("IRU_DEBUG_BUDGET", "1")
    budget = _make_budget()  # default similar limit for process_launch = 3

    cmds = [
        "Start-Process calc.exe",
        'Start-Process -FilePath "calc.exe"',
        "Start-Process calc",
        "Start-Process -FilePath calc.exe",  # triggers block
    ]

    results = []
    with caplog.at_level(logging.DEBUG, logger="iru.budget"):
        for cmd in cmds:
            results.append(budget.register("execute_cmd", cmd))

    # Last result must be blocked
    assert results[-1] == BUDGET_GUARD_ERROR

    # A BLOCK record must appear in the logs
    block_records = [
        r for r in caplog.records
        if r.name == "iru.budget" and "BLOCK" in r.getMessage()
    ]
    assert block_records, "Expected at least one [budget BLOCK] log record"
    assert "block_reason" in block_records[-1].getMessage()

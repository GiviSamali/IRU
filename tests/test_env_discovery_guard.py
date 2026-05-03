"""
tests/test_env_discovery_guard.py

Regression tests for EnvDiscoveryGuard and Python-environment-spiral detection.

Covers:
1. Python found + PyQt5 ModuleNotFoundError -> DEPENDENCY_MISSING_ERROR, no further
   interpreter search.
2. Repeated import-check for same package after failure -> blocked.
3. Missing PyQt5 is NOT classified as Python missing (CmdCategory stays
   environment_discovery for version-checks, not "interpreter not found").
4. pip install commands are package_install_or_setup, not read_only.
5. python -m pip --version is environment_discovery, not setup/mutating.
6. Existing budget thresholds still work (not broken by new guard).
"""
from __future__ import annotations

import pytest

from server.controller_budget import (
    DEPENDENCY_MISSING_ERROR,
    BUDGET_GUARD_ERROR,
    CmdCategory,
    CommandBudget,
    EnvDiscoveryGuard,
    classify_cmd,
    _extract_import_package,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _budget(
    max_post_found: int = 2,
    max_import_retries: int = 2,
    **kwargs,
) -> CommandBudget:
    """Budget with large caps so only env-guard / spiral limits fire."""
    defaults = dict(
        max_tool_calls=200,
        max_execute_cmd_calls=200,
        max_mutating_cmd_calls=200,
        max_environment_discovery_calls=200,
        max_read_only_cmd_calls=200,
        max_post_found_interpreter_searches=max_post_found,
        max_repeated_import_checks=max_import_retries,
    )
    defaults.update(kwargs)
    return CommandBudget(**defaults)


# ---------------------------------------------------------------------------
# 1. Python found + PyQt5 ModuleNotFoundError -> DEPENDENCY_MISSING_ERROR
# ---------------------------------------------------------------------------

def test_python_found_then_modulenotfounderror_stops_interpreter_search():
    """
    Sequence:
      python --version          -> OK (mark_interpreter_found)
      python -c "import PyQt5"  -> fails, record_import_failure("pyqt5")
      where python              -> should be BLOCKED (interpreter already found)
    """
    budget = _budget(max_post_found=0)  # 0 extra searches allowed after found

    assert budget.register("execute_cmd", "python --version") is None
    budget.mark_interpreter_found()

    assert budget.register("execute_cmd", 'python -c "import PyQt5"') is None
    budget.record_import_failure("PyQt5")

    result = budget.register("execute_cmd", "where python")
    assert result == DEPENDENCY_MISSING_ERROR, (
        f"Expected DEPENDENCY_MISSING_ERROR, got: {result!r}"
    )


def test_dependency_missing_error_not_budget_guard_error():
    """DEPENDENCY_MISSING_ERROR must be a different string from BUDGET_GUARD_ERROR."""
    assert DEPENDENCY_MISSING_ERROR != BUDGET_GUARD_ERROR
    assert "pip" in DEPENDENCY_MISSING_ERROR


# ---------------------------------------------------------------------------
# 2. Repeated import-check for same package -> blocked
# ---------------------------------------------------------------------------

def test_repeated_import_check_is_blocked_after_failure():
    budget = _budget(max_import_retries=2)

    assert budget.register("execute_cmd", 'python -c "import PyQt5"') is None
    budget.record_import_failure("PyQt5")

    assert budget.register("execute_cmd", 'python -c "import PyQt5"') is None
    assert budget.register("execute_cmd", 'python3 -c "import PyQt5"') is None
    result = budget.register("execute_cmd", 'py -c "import PyQt5"')
    assert result == DEPENDENCY_MISSING_ERROR, (
        f"Expected DEPENDENCY_MISSING_ERROR on 3rd retry, got: {result!r}"
    )


def test_different_packages_are_tracked_independently():
    budget = _budget(max_import_retries=1)

    assert budget.register("execute_cmd", 'python -c "import PyQt5"') is None
    budget.record_import_failure("PyQt5")

    assert budget.register("execute_cmd", 'python -c "import numpy"') is None

    result = budget.register("execute_cmd", 'python -c "import PyQt5"')
    assert result == DEPENDENCY_MISSING_ERROR


# ---------------------------------------------------------------------------
# 3. Missing package != Python missing
# ---------------------------------------------------------------------------

def test_missing_package_does_not_classify_as_python_missing():
    assert classify_cmd("python --version") == CmdCategory.ENVIRONMENT_DISCOVERY
    assert classify_cmd('python -c "import PyQt5"') == CmdCategory.ENVIRONMENT_DISCOVERY
    assert classify_cmd('python3 -c "import PyQt5; print(PyQt5.__version__)"') == CmdCategory.ENVIRONMENT_DISCOVERY


def test_extract_import_package_correct():
    cases = [
        ('python -c "import PyQt5"', "pyqt5"),
        ("python3 -c 'import PyQt5.QtWidgets'", "pyqt5"),
        ("py -c \"import numpy; print(numpy.__version__)\"", "numpy"),
        ('C:\\Python39\\python.exe -c "import PyQt5"', "pyqt5"),
        ("python --version", None),
        ("where python", None),
        ("pip install PyQt5", None),
    ]
    for cmd, expected in cases:
        result = _extract_import_package(cmd)
        assert result == expected, (
            f"_extract_import_package({cmd!r}): expected {expected!r}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# 4. pip install -> package_install_or_setup
# ---------------------------------------------------------------------------

def test_pip_install_classified_as_package_install():
    assert classify_cmd("pip install PyQt5") == CmdCategory.PACKAGE_INSTALL
    assert classify_cmd("pip3 install PyQt5") == CmdCategory.PACKAGE_INSTALL
    assert classify_cmd("python -m pip install PyQt5") == CmdCategory.PACKAGE_INSTALL
    assert classify_cmd("py -m pip install PyQt5") == CmdCategory.PACKAGE_INSTALL


def test_pip_install_not_readonly():
    assert classify_cmd("pip install PyQt5") != CmdCategory.READ_ONLY_INSPECTION
    assert classify_cmd("pip install PyQt5") != CmdCategory.ENVIRONMENT_DISCOVERY


# ---------------------------------------------------------------------------
# 5. pip --version -> environment_discovery, not mutating
# ---------------------------------------------------------------------------

def test_pip_version_check_classified_as_env_discovery():
    assert classify_cmd("python -m pip --version") == CmdCategory.ENVIRONMENT_DISCOVERY
    assert classify_cmd("py -m pip --version") == CmdCategory.ENVIRONMENT_DISCOVERY
    assert classify_cmd("pip --version") == CmdCategory.ENVIRONMENT_DISCOVERY
    assert classify_cmd("pip3 --version") == CmdCategory.ENVIRONMENT_DISCOVERY


def test_pip_version_does_not_count_as_mutating():
    budget = _budget()
    for _ in range(5):
        budget.register("execute_cmd", "python -m pip --version")
    assert budget.mutating_cmd_count == 0
    assert budget.environment_discovery_count == 5


# ---------------------------------------------------------------------------
# 6. Existing spiral detection still works
# ---------------------------------------------------------------------------

def test_start_process_spiral_still_blocked():
    budget = _budget()
    cmds = [
        "Start-Process calc.exe",
        'Start-Process -FilePath "calc.exe"',
        "Start-Process calc",
        "Start-Process -FilePath calc.exe",
    ]
    results = [budget.register("execute_cmd", cmd) for cmd in cmds]
    assert results[-1] == BUDGET_GUARD_ERROR
    assert all(r is None for r in results[:-1])


def test_interpreter_search_not_blocked_before_interpreter_found():
    """Before mark_interpreter_found(), interpreter searches pass normally."""
    budget = _budget(max_post_found=0)
    for cmd in [
        "python --version",
        "py --version",
        "where python",
        "Get-Command python",
    ]:
        result = budget.register("execute_cmd", cmd)
        assert result is None, (
            f"Expected pass before interpreter found, got {result!r} for {cmd!r}"
        )


# ---------------------------------------------------------------------------
# 7. EnvDiscoveryGuard unit tests (standalone)
# ---------------------------------------------------------------------------

def test_env_guard_standalone_interpreter_found_blocks_search():
    guard = EnvDiscoveryGuard(max_post_found_searches=1)
    guard.mark_interpreter_found()

    assert guard.check_command("where python") is None
    result = guard.check_command("python --version")
    assert result == DEPENDENCY_MISSING_ERROR


def test_env_guard_standalone_import_failure_blocks_repeat():
    guard = EnvDiscoveryGuard(max_repeated_import_checks=1)
    guard.record_import_failure("pyqt5")

    assert guard.check_command('python -c "import pyqt5"') is None
    result = guard.check_command('python3 -c "import pyqt5"')
    assert result == DEPENDENCY_MISSING_ERROR


def test_env_guard_no_block_before_failure_recorded():
    guard = EnvDiscoveryGuard(max_repeated_import_checks=0)
    assert guard.check_command('python -c "import PyQt5"') is None

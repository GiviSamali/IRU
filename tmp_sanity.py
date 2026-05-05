from __future__ import annotations

from server.controller_budget import CommandBudget, normalize_execute_cmd, _is_interpreter_search_cmd, _extract_import_package


def _apply_patch_sanity():
    budget = CommandBudget()
    budget.observe_execute_result("python --version", {"returncode": 0, "stdout": "Python 3.11.0", "stderr": ""})
    assert budget.env_guard.interpreter_found is True
    budget.observe_execute_result('python -c "import PyQt5"', {"returncode": 1, "stdout": "", "stderr": "ModuleNotFoundError: No module named 'PyQt5'"})
    assert "pyqt5" in budget.env_guard.failed_import_retries

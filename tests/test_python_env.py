from server.controller_budget import DEPENDENCY_MISSING_ERROR, CommandBudget
from server.python_env import (
    EnvDiscoveryGuard,
    classify_python_env_result,
    parse_import_check_result,
    parse_pip_version_result,
    parse_python_version_result,
)


def test_python_found_and_pyqt5_missing_classifies_dependency_missing():
    result = {
        "returncode": 1,
        "stdout": "",
        "stderr": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'PyQt5'\n",
    }

    parsed = classify_python_env_result('python -c "import PyQt5"', result)

    assert parsed["status"] == "dependency_missing"
    assert parsed["python_found"] is True
    assert parsed["package"] == "PyQt5"


def test_python_missing_is_not_dependency_missing():
    result = {
        "returncode": 9009,
        "stdout": "",
        "stderr": "Python was not found; run without arguments to install from the Microsoft Store.",
    }

    parsed = parse_python_version_result(result)

    assert parsed == {"status": "python_missing", "python_found": False}


def test_python_sys_version_output_classifies_python_found():
    result = {"returncode": 0, "stdout": "3.11.7 (main, Jan 1 2026)\n", "stderr": ""}

    parsed = classify_python_env_result('python -c "import sys; print(sys.version)"', result)

    assert parsed["status"] == "python_found"
    assert parsed["version"] == "3.11.7"


def test_pip_missing_classifies_pip_missing():
    result = {
        "returncode": 1,
        "stdout": "",
        "stderr": "C:\\Python311\\python.exe: No module named pip",
    }

    parsed = parse_pip_version_result(result)

    assert parsed["status"] == "pip_missing"
    assert parsed["pip_found"] is False


def test_import_check_ok_marks_python_found():
    result = {"returncode": 0, "stdout": "", "stderr": ""}

    parsed = parse_import_check_result(result)

    assert parsed["status"] == "import_ok"
    assert parsed["python_found"] is True


def test_repeated_import_check_after_missing_package_is_stopped():
    guard = EnvDiscoveryGuard()
    missing_result = {
        "returncode": 1,
        "stdout": "",
        "stderr": "ModuleNotFoundError: No module named 'PyQt5'",
    }

    first_stop = guard.observe('python -c "import PyQt5"', missing_result)
    second_stop = guard.before_execute('py -c "import PyQt5"')

    assert first_stop is not None
    assert second_stop is not None
    assert "PyQt5" in second_stop
    assert "Missing dependency does not mean Python is missing" in second_stop


def test_no_further_interpreter_search_after_interpreter_found():
    budget = CommandBudget()

    assert budget.register("execute_cmd", "python --version") is None
    assert budget.observe_execute_result(
        "python --version",
        {"returncode": 0, "stdout": "Python 3.11.7\r\n", "stderr": ""},
    ) is None

    stopped = budget.register("execute_cmd", "py --version")

    assert stopped == DEPENDENCY_MISSING_ERROR


def test_command_budget_stops_after_dependency_missing_result():
    budget = CommandBudget()

    assert budget.register("execute_cmd", 'python -c "import PyQt5"') is None
    stopped = budget.observe_execute_result(
        'python -c "import PyQt5"',
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'PyQt5'",
        },
    )

    assert stopped == DEPENDENCY_MISSING_ERROR

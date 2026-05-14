from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE
from server.python_env import classify_command_error


def test_classify_command_error_extracts_multiple_missing_packages():
    result = {
        "returncode": 1,
        "stdout": "",
        "stderr": "\n".join([
            "Traceback (most recent call last):",
            "ModuleNotFoundError: No module named 'PyQt5'",
            "Traceback (most recent call last):",
            "ModuleNotFoundError: No module named 'numpy'",
            "Traceback (most recent call last):",
            "ModuleNotFoundError: No module named 'matplotlib'",
        ]),
    }

    classified = classify_command_error(result, "python -c import-checks")

    assert classified["error_type"] == "dependency_missing"
    assert classified["recoverable"] is True
    assert classified["missing_packages"] == ["PyQt5", "numpy", "matplotlib"]


def test_classify_command_error_shell_parser_error():
    classified = classify_command_error(
        {
            "returncode": 1,
            "stderr": "ParserError: Missing expression after unary operator '-'",
        },
        "powershell bad syntax",
    )

    assert classified["error_type"] == "shell_syntax_error"
    assert classified["recoverable"] is True


def test_classify_command_error_path_missing():
    classified = classify_command_error(
        {
            "returncode": 1,
            "stderr": "Cannot find path 'C:\\missing' because it does not exist.",
        },
        "Get-Content C:\\missing",
    )

    assert classified["error_type"] == "path_missing"
    assert classified["recoverable"] is True


def test_classify_command_error_command_missing():
    classified = classify_command_error(
        {
            "returncode": 1,
            "stderr": "CommandNotFoundException: foo is not recognized as the name of a cmdlet",
        },
        "foo --version",
    )

    assert classified["error_type"] == "command_missing"
    assert classified["recoverable"] is True


def test_prompt_contains_non_throwing_package_check_example():
    assert "importlib.util.find_spec" in SYSTEM_PROMPT_TEMPLATE
    assert "Do not chain many import checks" in SYSTEM_PROMPT_TEMPLATE
    assert "import PyQt5; PyQt5.QtCore" not in SYSTEM_PROMPT_TEMPLATE

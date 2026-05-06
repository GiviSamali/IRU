from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


PYTHON_ENV_STOP_ERROR = (
    "Python environment check stopped: Python was found, but the requested "
    "package is missing in that interpreter. Do not search for another "
    "interpreter without an explicit reason; ask for confirmation before "
    "installing the dependency."
)


def _result_text(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return ""
    parts = [
        result.get("stdout"),
        result.get("stderr"),
        result.get("error"),
        result.get("result"),
    ]
    return "\n".join(str(part) for part in parts if part is not None)


def _returncode(result: dict[str, Any] | None) -> int | None:
    if not isinstance(result, dict):
        return None
    value = result.get("returncode")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_missing_executable_text(text: str) -> bool:
    lowered = text.lower()
    missing_markers = (
        "python was not found",
        "not recognized as an internal or external command",
        "is not recognized as the name of a cmdlet",
        "command not found",
        "no such file or directory",
        "executable file not found",
        "cannot find path",
    )
    return any(marker in lowered for marker in missing_markers)


def _module_not_found_package(text: str) -> str | None:
    match = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
    if match:
        return match.group(1)
    return None


def _python_version(text: str) -> str | None:
    match = re.search(r"\bPython\s+(\d+\.\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"^\s*(\d+\.\d+(?:\.\d+)?)\b", text, re.MULTILINE)
    if match:
        return match.group(1)
    return None


def _python_executable(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if re.search(r"python(?:\d+(?:\.\d+)*)?(\.exe)?$", candidate, re.IGNORECASE):
            if re.search(r"[/\\]", candidate) or re.match(r"^[a-zA-Z]:", candidate):
                return candidate
    return None


def _pip_version(text: str) -> str | None:
    match = re.search(r"\bpip\s+(\d+(?:\.\d+)+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _command_lower(command: str) -> str:
    cleaned = re.sub(r"['\"`]", "", (command or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def is_python_import_check(command: str) -> bool:
    lowered = _command_lower(command)
    return bool(re.match(r"^(py|python|python3(?:\.\d+)?)\s+-c\s+", lowered)) and (
        "import " in lowered or "__import__(" in lowered
    )


def is_python_interpreter_discovery(command: str) -> bool:
    lowered = _command_lower(command)
    if re.match(r"^(py|python|python3(?:\.\d+)?)\s+(-v|--version|-version)$", lowered):
        return True
    if re.match(r"^(where|get-command|which)\s+(py|python|python3(?:\.\d+)?)$", lowered):
        return True
    if re.match(r"^(py|python|python3(?:\.\d+)?)\s+-c\s+", lowered):
        return any(marker in lowered for marker in ("sys.executable", "sys.version", "site.getsitepackages"))
    return False


def is_pip_version_check(command: str) -> bool:
    lowered = _command_lower(command)
    return bool(
        re.match(r"^(pip|pip3)\s+(-v|--version|-version)$", lowered)
        or re.match(r"^(py|python|python3(?:\.\d+)?)\s+-m\s+pip\s+(-v|--version|-version)$", lowered)
    )


def parse_python_version_result(result: dict[str, Any] | None) -> dict[str, Any]:
    text = _result_text(result)
    version = _python_version(text)
    executable = _python_executable(text)
    if version or executable:
        return {
            "status": "python_found",
            "python_found": True,
            "version": version,
            "executable": executable,
        }
    if _is_missing_executable_text(text):
        return {"status": "python_missing", "python_found": False}
    return {"status": "unknown"}


def parse_pip_version_result(result: dict[str, Any] | None) -> dict[str, Any]:
    text = _result_text(result)
    if "no module named pip" in text.lower() or _is_missing_executable_text(text):
        return {"status": "pip_missing", "pip_found": False}
    version = _pip_version(text)
    if version or _returncode(result) == 0:
        return {"status": "pip_found", "pip_found": True, "version": version}
    return {"status": "unknown"}


def parse_import_check_result(result: dict[str, Any] | None) -> dict[str, Any]:
    text = _result_text(result)
    package = _module_not_found_package(text)
    if package:
        return {
            "status": "dependency_missing",
            "python_found": True,
            "package": package,
        }
    if _is_missing_executable_text(text):
        return {"status": "python_missing", "python_found": False}
    if _returncode(result) == 0:
        return {"status": "import_ok", "python_found": True}
    return {"status": "unknown"}


def classify_python_env_result(command: str, result: dict[str, Any] | None) -> dict[str, Any]:
    text = _result_text(result)
    package = _module_not_found_package(text)
    if package:
        return {
            "status": "dependency_missing",
            "python_found": True,
            "package": package,
        }
    if is_pip_version_check(command):
        return parse_pip_version_result(result)
    if is_python_interpreter_discovery(command):
        return parse_python_version_result(result)
    if is_python_import_check(command):
        return parse_import_check_result(result)
    return {"status": "unknown"}


@dataclass
class EnvDiscoveryGuard:
    python_found: bool = False
    python_missing: bool = False
    python_version: str | None = None
    python_executable: str | None = None
    pip_found: bool = False
    pip_missing: bool = False
    missing_packages: set[str] = field(default_factory=set)

    def before_execute(self, command: str) -> str | None:
        if self.missing_packages and is_python_interpreter_discovery(command):
            package = sorted(self.missing_packages)[0]
            return self._dependency_error(package)
        if self.missing_packages and is_python_import_check(command):
            package = sorted(self.missing_packages)[0]
            return self._dependency_error(package)
        if self.python_found and is_python_interpreter_discovery(command):
            return (
                "Python environment check stopped: Python is already confirmed "
                "for this task. Do not search for another interpreter without "
                "an explicit reason."
            )
        return None

    def observe(self, command: str, result: dict[str, Any] | None) -> str | None:
        classified = classify_python_env_result(command, result)
        status = classified.get("status")
        if status in {"python_found", "import_ok"}:
            self.python_found = True
            self.python_missing = False
            self.python_version = classified.get("version") or self.python_version
            self.python_executable = classified.get("executable") or self.python_executable
        elif status == "python_missing":
            self.python_missing = True
        elif status == "pip_found":
            self.pip_found = True
            self.pip_missing = False
        elif status == "pip_missing":
            self.pip_missing = True
        elif status == "dependency_missing":
            self.python_found = True
            self.python_missing = False
            package = str(classified.get("package") or "").strip()
            if package:
                self.missing_packages.add(package)
            return self._dependency_error(package)
        return None

    def _dependency_error(self, package: str | None) -> str:
        if package:
            return (
                f"Python environment check stopped: package '{package}' is "
                "missing in the selected Python interpreter. Missing dependency "
                "does not mean Python is missing. Do not search for another "
                "interpreter; ask for confirmation before installing the dependency."
            )
        return PYTHON_ENV_STOP_ERROR

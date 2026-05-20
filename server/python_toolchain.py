from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


TOOLCHAIN_STATUSES = {"ok", "missing", "broken_stub", "install_required", "error"}

PYTHON_TOOLCHAIN_DISCOVERY_GUIDANCE = "\n".join(
    [
        "Python resolver discovery order on Windows:",
        'A. py -3 -c "import sys; print(sys.executable); print(sys.version)"',
        r"B. common paths: C:\Program Files\Python*\python.exe and C:\Users\<user>\AppData\Local\Programs\Python\Python*\python.exe",
        "C. bare python/python3 only if it is not WindowsApps stub and returns sys.executable with a valid version.",
    ]
)


@dataclass
class PythonToolchainReceipt:
    device_id: str | None = None
    status: str = "missing"
    interpreter_path: str | None = None
    launcher: str | None = None
    version: str | None = None
    pip_available: bool | None = None
    pip_version: str | None = None
    site_packages: list[str] = field(default_factory=list)
    packages: dict[str, str] = field(default_factory=dict)
    raw_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_any(cls, value: Any) -> "PythonToolchainReceipt | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        receipt = cls(**data)
        if receipt.status not in TOOLCHAIN_STATUSES:
            receipt.status = "error"
        receipt.site_packages = list(receipt.site_packages or [])
        receipt.packages = dict(receipt.packages or {})
        receipt.raw_evidence = list(receipt.raw_evidence or [])
        if receipt.status == "ok" and not is_verified_python_receipt(receipt):
            receipt.status = "error"
        return receipt


_RECEIPT_CACHE: dict[str, PythonToolchainReceipt] = {}


def _cache_keys(device_context: dict[str, Any] | None) -> list[str]:
    context = device_context or {}
    keys = []
    for key in ("device_id", "machine_guid"):
        value = str(context.get(key) or "").strip()
        if value:
            keys.append(value)
    return keys


def get_cached_python_toolchain(device_context: dict[str, Any] | None) -> PythonToolchainReceipt | None:
    for key in _cache_keys(device_context):
        receipt = _RECEIPT_CACHE.get(key)
        if receipt:
            return receipt
    return None


def remember_python_toolchain(receipt: PythonToolchainReceipt) -> None:
    if receipt.status == "ok" and not is_verified_python_receipt(receipt):
        return
    if receipt.status not in {"ok", "broken_stub", "install_required"}:
        return
    if receipt.device_id:
        _RECEIPT_CACHE[str(receipt.device_id)] = receipt


def python_toolchain_from_runtime_summary(
    summary: dict[str, Any] | None,
    *,
    device_id: str | None = None,
) -> PythonToolchainReceipt | None:
    if not isinstance(summary, dict):
        return None
    if summary.get("runtime_status") != "ok":
        return None
    interpreter = str(summary.get("venv_python") or "").strip()
    version = str(summary.get("python_version") or "").strip()
    if not interpreter or not version:
        return None
    receipt = PythonToolchainReceipt(
        device_id=device_id or summary.get("device_id"),
        status="ok",
        interpreter_path=interpreter,
        launcher=interpreter,
        version=version,
        pip_available=summary.get("pip_status") == "ok",
        pip_version=None,
        site_packages=[],
        packages={},
        raw_evidence=["managed_python_runtime_summary"],
        confidence=0.99,
    )
    if is_verified_python_receipt(receipt):
        remember_python_toolchain(receipt)
        return receipt
    return None


def _result_text(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return ""
    return "\n".join(
        str(result.get(key))
        for key in ("stdout", "stderr", "error", "result")
        if result.get(key) is not None
    )


def _returncode(result: dict[str, Any] | None) -> int | None:
    if not isinstance(result, dict):
        return None
    try:
        return int(result.get("returncode"))
    except (TypeError, ValueError):
        return None


def _normalize_path(value: str | None) -> str:
    return (value or "").strip().strip('"').replace("/", "\\").lower()


def _is_absolute_path(path: str | None) -> bool:
    value = (path or "").strip().strip('"')
    return bool(re.match(r"^[a-zA-Z]:[\\/]", value) or value.startswith("/") or value.startswith("\\\\"))


def is_windowsapps_stub_path(path: str | None) -> bool:
    normalized = _normalize_path(path)
    return bool(
        normalized.endswith("\\appdata\\local\\microsoft\\windowsapps\\python.exe")
        or normalized.endswith("\\appdata\\local\\microsoft\\windowsapps\\python3.exe")
    )


def is_verified_python_receipt(receipt: PythonToolchainReceipt | None) -> bool:
    if not receipt or receipt.status != "ok":
        return False
    return bool(
        receipt.interpreter_path
        and _is_absolute_path(receipt.interpreter_path)
        and not is_windowsapps_stub_path(receipt.interpreter_path)
        and receipt.version
        and receipt.confidence >= 0.9
    )


def _is_common_windows_install(path: str | None) -> bool:
    normalized = _normalize_path(path)
    return bool(
        re.match(r"^[a-z]:\\program files\\python\d+\\python\.exe$", normalized)
        or re.match(r"^[a-z]:\\users\\[^\\]+\\appdata\\local\\programs\\python\\python\d+\\python\.exe$", normalized)
    )


def _version(text: str) -> str | None:
    match = re.search(r"\bPython\s+(\d+\.\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"^\s*(\d+\.\d+(?:\.\d+)?)\b", text, re.MULTILINE)
    return match.group(1) if match else None


def _pip_version(text: str) -> str | None:
    match = re.search(r"\bpip\s+(\d+(?:\.\d+)+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _executable(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip().strip('"')
        if re.search(r"(?i)(?:^|[\\/])python(?:\d+(?:\.\d+)*)?\.exe$", candidate):
            if "\\" in candidate or "/" in candidate or re.match(r"^[a-zA-Z]:", candidate):
                return candidate
    return None


def _site_packages(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        for part in re.split(r"[;|]", line.strip()):
            candidate = part.strip().strip("'\"[]")
            if "site-packages" in candidate.lower() and candidate not in found:
                found.append(candidate)
    return found


def _command_text(command: str | None) -> str:
    value = (command or "").strip()
    value = re.sub(
        r"^\[Console\]::OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*"
        r"\$OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _launcher(command: str | None) -> str | None:
    command = _command_text(command)
    if not command:
        return None
    match = re.match(r"^&\s*\"([^\"]+python(?:\d*)?\.exe)\"", command, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.match(r"^\"([^\"]+python(?:\d*)?\.exe)\"", command, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.match(r"^([a-zA-Z]:\\[^\s]+python(?:\d*)?\.exe)", command, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.match(r"^(py(?:\s+-3)?|python3?|pip3?)\b", command, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _candidate_priority(launcher: str | None, executable: str | None) -> int:
    launcher_norm = (launcher or "").lower()
    if launcher_norm.startswith("py"):
        return 10
    if _is_common_windows_install(executable or launcher):
        return 20
    if launcher_norm == "python":
        return 30
    if launcher_norm == "python3":
        return 40
    return 50


def _is_python_stub(command: str | None, result: dict[str, Any] | None, path: str | None = None) -> bool:
    text = _result_text(result).strip()
    launcher = _launcher(command)
    if is_windowsapps_stub_path(path) or is_windowsapps_stub_path(launcher):
        return True
    if "source version 0.0.0.0" in text.lower():
        return True
    if text.lower() == "python" and _returncode(result) not in (0, None):
        return True
    return False


def _iter_command_results(command_results: Any) -> list[dict[str, Any]]:
    if not command_results:
        return []
    if isinstance(command_results, dict):
        items = []
        for command, result in command_results.items():
            if isinstance(result, dict) and "command" in result and "result" in result:
                items.append(result)
            else:
                items.append({"command": command, "result": result})
        return items
    items = []
    for item in command_results:
        if not isinstance(item, dict):
            continue
        if "result" in item:
            items.append(item)
        elif "command" in item:
            items.append({"command": item.get("command"), "result": item})
    return items


def _packages_from_command(command: str | None) -> list[str]:
    command = _command_text(command)
    packages: list[str] = []
    import_match = re.search(r"(?:^|\s)-c\s+['\"]?.*?\bimport\s+([A-Za-z_][A-Za-z0-9_\.]*)", command)
    if import_match:
        packages.append(import_match.group(1).split(".")[0])
    install_match = re.search(r"\b-m\s+pip\s+install\s+(.+)$|\bpip3?\s+install\s+(.+)$", command, re.IGNORECASE)
    if install_match:
        raw = install_match.group(1) or install_match.group(2) or ""
        for token in re.split(r"\s+", raw):
            clean = token.strip().strip("'\"")
            if clean and not clean.startswith("-") and not re.search(r"[<>=]", clean):
                packages.append(clean)
    return packages


def resolve_python_toolchain(
    device_context: dict[str, Any] | None,
    command_results: Any = None,
) -> PythonToolchainReceipt:
    context = device_context or {}
    device_id = str(context.get("device_id") or context.get("machine_guid") or "").strip() or None
    raw_items = _iter_command_results(command_results)
    cached = PythonToolchainReceipt.from_any(context.get("python_toolchain_receipt")) or get_cached_python_toolchain(context)

    candidates: list[tuple[int, PythonToolchainReceipt]] = []
    broken_aliases: set[str] = set()
    packages: dict[str, str] = dict((cached.packages if cached else {}) or {})
    raw_evidence: list[str] = []
    pip_available = cached.pip_available if cached else None
    pip_version = cached.pip_version if cached else None
    site_packages = list(cached.site_packages if cached else [])

    if cached and is_verified_python_receipt(cached):
        candidates.append((0, cached))
        raw_evidence.extend(cached.raw_evidence[-4:])

    for item in raw_items:
        command = str(item.get("command") or "")
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        text = _result_text(result)
        launcher = _launcher(command)
        executable = _executable(text) or (launcher if launcher and re.match(r"^[a-zA-Z]:\\", launcher) else None)
        version = _version(text)
        rc = _returncode(result)
        evidence = f"{command[:120]} => rc={rc} {text[:180]}".strip()

        if _is_python_stub(command, result, executable):
            alias = "python3" if (launcher or "").lower().startswith("python3") else "python"
            broken_aliases.add(alias)
            raw_evidence.append(f"broken_alias:{alias}:{evidence}")
            continue

        if "no module named pip" in text.lower():
            pip_available = False
            raw_evidence.append(f"pip_missing:{evidence}")
        elif _pip_version(text) or (re.search(r"\b-m\s+pip\s+(?:--version|-V|-v)\b", command, re.IGNORECASE) and rc == 0):
            pip_available = True
            pip_version = _pip_version(text) or pip_version
            raw_evidence.append(f"pip_ok:{evidence}")

        for path in _site_packages(text):
            if path not in site_packages:
                site_packages.append(path)

        missing_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
        if missing_match:
            packages[missing_match.group(1).split(".")[0]] = "missing"
        elif rc == 0:
            for package in _packages_from_command(command):
                packages[package] = "installed"

        if not version and not executable:
            continue
        if executable and is_windowsapps_stub_path(executable):
            broken_aliases.add((launcher or "python").split()[0])
            raw_evidence.append(f"broken_alias:{launcher or 'python'}:{evidence}")
            continue
        if version and executable and _is_absolute_path(executable) and not is_windowsapps_stub_path(executable) and rc == 0:
            receipt = PythonToolchainReceipt(
                device_id=device_id,
                status="ok",
                interpreter_path=executable,
                launcher=launcher,
                version=version,
                pip_available=pip_available,
                pip_version=pip_version,
                site_packages=site_packages,
                packages=packages,
                raw_evidence=[evidence],
                confidence=0.96 if executable else 0.82,
            )
            candidates.append((_candidate_priority(launcher, receipt.interpreter_path), receipt))

    if candidates:
        _, winner = sorted(candidates, key=lambda item: item[0])[0]
        winner.device_id = device_id or winner.device_id
        winner.pip_available = pip_available
        winner.pip_version = pip_version
        winner.site_packages = site_packages
        winner.packages = packages
        winner.raw_evidence = [*winner.raw_evidence, *raw_evidence][-12:]
        winner.confidence = max(winner.confidence, 0.9)
        if not is_verified_python_receipt(winner):
            return PythonToolchainReceipt(
                device_id=device_id,
                status="missing",
                packages=packages,
                raw_evidence=[*winner.raw_evidence, *raw_evidence][-12:],
                confidence=0.0,
            )
        remember_python_toolchain(winner)
        return winner

    if broken_aliases:
        receipt = PythonToolchainReceipt(
            device_id=device_id,
            status="broken_stub",
            interpreter_path=None,
            launcher=None,
            version=None,
            pip_available=False,
            packages=packages,
            raw_evidence=raw_evidence[-12:],
            confidence=0.86,
        )
        remember_python_toolchain(receipt)
        return receipt

    return PythonToolchainReceipt(
        device_id=device_id,
        status="missing",
        packages=packages,
        raw_evidence=raw_evidence[-12:],
        confidence=0.0,
    )


def known_broken_aliases(receipt: PythonToolchainReceipt | None) -> list[str]:
    aliases: set[str] = set()
    for evidence in (receipt.raw_evidence if receipt else []) or []:
        match = re.search(r"broken_alias:(python3?|py)", evidence, re.IGNORECASE)
        if match:
            aliases.add(match.group(1).lower())
    return sorted(aliases)


def build_python_toolchain_block(receipt: PythonToolchainReceipt | None) -> str:
    receipt = receipt or PythonToolchainReceipt()
    aliases = known_broken_aliases(receipt)
    pip_status = "unknown"
    if receipt.pip_available is True:
        pip_status = f"available ({receipt.pip_version})" if receipt.pip_version else "available"
    elif receipt.pip_available is False:
        pip_status = "missing"
    packages = ", ".join(f"{name}={status}" for name, status in sorted((receipt.packages or {}).items())) or "unknown"
    return "\n".join(
        [
            "## Target device Python toolchain",
            f"status: {receipt.status}",
            f"resolved_python_path: {receipt.interpreter_path or ''}",
            f"python_version: {receipt.version or ''}",
            f"pip_status: {pip_status}",
            f"known_broken_aliases: {', '.join(aliases) if aliases else ''}",
            f"packages: {packages}",
            "Use resolved_python_path, not bare python, if provided.",
            'For PowerShell use: & "<resolved_python_path>" -m pip ... and & "<resolved_python_path>" script.py',
            PYTHON_TOOLCHAIN_DISCOVERY_GUIDANCE,
        ]
    )


def _split_powershell_statements(command: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in command:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "`":
            current.append(ch)
            escaped = True
            continue
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
            continue
        if ch == ";":
            statements.append("".join(current))
            current = []
            continue
        current.append(ch)
    statements.append("".join(current))
    return statements


def _rewrite_python_statement(statement: str, interpreter_path: str) -> str | None:
    stripped = statement.strip()
    leading = statement[: len(statement) - len(statement.lstrip())]
    match = re.match(r"^(python3?|py(?:\s+-3)?|pip3?)\b\s*(.*)$", stripped, re.IGNORECASE)
    if not match:
        return None
    launcher = match.group(1).lower()
    rest = match.group(2).strip()
    ps_python = f'& "{interpreter_path}"'
    if launcher.startswith("pip"):
        return f"{leading}{ps_python} -m pip{(' ' + rest) if rest else ''}"
    return f"{leading}{ps_python}{(' ' + rest) if rest else ''}"


def _contains_bare_python_invocation(command: str) -> bool:
    invocation_re = re.compile(
        r"(?i)(?:^\s*&?\s*|[{\(|]\s*&?\s*)(python3?|py(?:\s+-3)?|pip3?)\b"
    )
    for statement in _split_powershell_statements(_command_text(command)):
        if invocation_re.search(statement):
            return True
    return False


def rewrite_python_command(command: str, receipt: PythonToolchainReceipt | None) -> tuple[str, str | None]:
    if not command:
        return command, None
    if not receipt or receipt.status != "ok" or not receipt.interpreter_path:
        if receipt and receipt.status == "broken_stub" and _contains_bare_python_invocation(command):
            return command, "Python command blocked: bare python/pip alias is a known WindowsApps stub; resolve or install real Python first."
        return command, None
    if not is_verified_python_receipt(receipt):
        if _contains_bare_python_invocation(command):
            return command, "Python command blocked: resolved Python receipt is not verified."
        return command, None

    statements = _split_powershell_statements(command)
    changed = False
    rewritten: list[str] = []
    for statement in statements:
        replacement = _rewrite_python_statement(statement, receipt.interpreter_path)
        if replacement is None:
            rewritten.append(statement)
        else:
            rewritten.append(replacement)
            changed = True
    if changed:
        return ";".join(rewritten), None
    if _contains_bare_python_invocation(command):
        return command, "Python command blocked: bare python/pip invocation could not be safely rewritten."
    return command, None


def validate_toolchain_fact_against_receipt(
    fact: str,
    receipt: PythonToolchainReceipt | None,
) -> tuple[bool, str | None]:
    text = (fact or "").strip()
    if not text:
        return False, None
    lower = text.lower()
    needs_receipt = bool("python" in lower or "pyqt" in lower or "pyqt5" in lower or "toolchain" in lower)
    if not needs_receipt:
        return True, text
    if not is_verified_python_receipt(receipt):
        return False, None

    corrected = text
    version_match = re.search(r"\bPython\s+(\d+\.\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if version_match and version_match.group(1) != receipt.version:
        corrected = corrected[:version_match.start(1)] + receipt.version + corrected[version_match.end(1):]

    negative_markers = (
        "not installed",
        "not found",
        "не установлен",
        "не установлена",
        "не установлены",
        "не найден",
        "не найдена",
        "не найдено",
        "отсутствует",
        "не распознан",
        "не распознано",
    )
    if any(marker in lower for marker in negative_markers):
        return False, None

    pyqt_status = (receipt.packages or {}).get("PyQt5") or (receipt.packages or {}).get("pyqt5")
    if "pyqt" in lower and pyqt_status != "installed":
        return False, None

    if "python" in lower and receipt.interpreter_path not in corrected and re.search(r"установ|install|available|доступ", lower):
        corrected = f"Python {receipt.version} is installed at {receipt.interpreter_path}."
        if pyqt_status == "installed":
            corrected += " PyQt5 is installed in that interpreter."
    return True, corrected

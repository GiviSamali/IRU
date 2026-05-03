"""
controller_budget.py

Command budget guard shared by non-pipeline and pipeline controllers.

Commands are classified into categories, each with its own similarity limit:

  Category                 | Similar-limit | Counts toward mutating budget?
  ─────────────────────────|───────────────|───────────────────────────────
  environment_discovery    |      5        |  NO  (own counter)
  read_only_inspection     |      6        |  NO  (own counter)
  package_install_or_setup |      4        |  YES (mutating counter)
  process_launch           |      3        |  YES (mutating counter)
  destructive              |      2        |  YES (mutating counter)
  unknown                  |      3        |  YES (mutating counter)

Hard caps (never exceeded regardless of category):
  max_tool_calls_per_task       = 30   (all tool calls combined)
  max_mutating_cmd_calls        = 15   (mutating execute_cmd only)
  max_environment_discovery     = 15   (environment discovery only)
  max_read_only_cmd_calls       = 30   (read-only execute_cmd only)

EnvDiscoveryGuard:
  Detects Python-environment spiral:
    1. Once a working interpreter is confirmed (python --version exits 0),
       further interpreter searches are blocked (MAX_POST_FOUND_INTERPRETER_SEARCHES).
    2. Once an import-check for a package fails with ModuleNotFoundError,
       repeating the same import-check is blocked (MAX_REPEATED_IMPORT_CHECKS).
  The guard does NOT install packages and does NOT assume the package exists.
  It only stops the search spiral and surfaces DEPENDENCY_MISSING_ERROR.

Debug logging:
  Set env var IRU_DEBUG_BUDGET=1 to enable verbose per-call budget logs.
  Off by default — zero overhead in production.
"""
from __future__ import annotations

import logging
import os
import re
from enum import Enum

# ---------------------------------------------------------------------------
# Debug logger
# ---------------------------------------------------------------------------

_log = logging.getLogger("iru.budget")

def _debug_enabled() -> bool:
    return os.environ.get("IRU_DEBUG_BUDGET", "").strip() == "1"


# ---------------------------------------------------------------------------
# Hard caps
# ---------------------------------------------------------------------------

MAX_TOOL_CALLS_PER_TASK = 30
MAX_MUTATING_CMD_CALLS = 15
MAX_ENVIRONMENT_DISCOVERY_CALLS = 15
MAX_READ_ONLY_CMD_CALLS = 30

# Per-key similarity limits per category
MAX_SIMILAR_ENVIRONMENT_DISCOVERY = 5
MAX_SIMILAR_READ_ONLY = 6
MAX_SIMILAR_SETUP = 4
MAX_SIMILAR_PROCESS_LAUNCH = 3
MAX_SIMILAR_DESTRUCTIVE = 2
MAX_SIMILAR_UNKNOWN = 3

# EnvDiscoveryGuard thresholds
MAX_POST_FOUND_INTERPRETER_SEARCHES = 2  # how many extra interpreter-find cmds after found
MAX_REPEATED_IMPORT_CHECKS = 2           # how many times same failed import may be retried

# Legacy aliases
MAX_EXECUTE_CMD_CALLS_PER_TASK = MAX_MUTATING_CMD_CALLS
MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK = MAX_SIMILAR_UNKNOWN
MAX_SIMILAR_READONLY_CALLS_PER_TASK = MAX_SIMILAR_READ_ONLY
MAX_SIMILAR_INSTALL_CALLS_PER_TASK = MAX_SIMILAR_SETUP

BUDGET_GUARD_ERROR = (
    "Я остановился: было выполнено несколько похожих попыток, но надёжно подтвердить результат не удалось. "
    "Чтобы не выполнять лишние команды, продолжение остановлено."
)

DEPENDENCY_MISSING_ERROR = (
    "Python-интерпретатор найден, но модуль не установлен. "
    "Необходимо установить зависимость через pip install. "
    "Продолжение без установки невозможно — дальнейший поиск интерпретатора остановлен."
)


# ---------------------------------------------------------------------------
# Category enum
# ---------------------------------------------------------------------------

class CmdCategory(str, Enum):
    ENVIRONMENT_DISCOVERY = "environment_discovery"
    READ_ONLY_INSPECTION  = "read_only_inspection"
    PACKAGE_INSTALL       = "package_install_or_setup"
    PROCESS_LAUNCH        = "process_launch"
    DESTRUCTIVE           = "destructive"
    UNKNOWN               = "unknown"


# ---------------------------------------------------------------------------
# Verb sets
# ---------------------------------------------------------------------------

_READ_ONLY_VERBS: frozenset[str] = frozenset({
    "get-childitem", "get-content", "test-path", "resolve-path",
    "select-string", "get-process", "get-service",
    "dir", "ls", "cat", "type", "grep", "findstr",
})

_INSTALL_VERBS: frozenset[str] = frozenset({"pip", "pip3"})

_DESTRUCTIVE_VERBS: frozenset[str] = frozenset({
    "remove-item", "del", "rm", "rmdir", "rd",
    "format", "clear-content", "clear-variable",
    "stop-process", "kill",
    "invoke-expression", "iex",
})

_PROCESS_LAUNCH_VERBS: frozenset[str] = frozenset({
    "start-process", "start",
})

# Commands that indicate "I am searching for a Python interpreter"
_INTERPRETER_SEARCH_KEYS: frozenset[str] = frozenset({
    "python --version",
    "python -v",
    "where python",
    "get-command python",
    "which python",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_primary_path(parts: list[str]) -> str:
    for p in parts:
        if re.search(r"[/\\]", p) or re.match(r"[a-zA-Z]:", p):
            return p.lower()
    return parts[1].lower() if len(parts) > 1 else ""


def _extract_import_package(command: str) -> str | None:
    """
    If the command is a Python import-check (-c "import X" or -c "import X; ..."),
    return the normalised package name, else None.

    Handles:
      python -c "import PyQt5"
      python3 -c 'import PyQt5; print(...)'
      py -c "import PyQt5.QtWidgets"
      C:\\Python39\\python.exe -c "import PyQt5"
    """
    m = re.search(
        r"(?:python[0-9.]*|py(?:thon[0-9.]*)?)(?:\.exe)?\s+.*?-c\s+[\"']?import\s+([A-Za-z_][A-Za-z0-9_.]*)",
        command,
        re.IGNORECASE,
    )
    if m:
        # Normalise to top-level package name (PyQt5.QtWidgets -> pyqt5)
        return m.group(1).split(".")[0].lower()
    return None


def _is_interpreter_search_cmd(key: str) -> bool:
    """True if the normalised key is an interpreter-search command."""
    return key in _INTERPRETER_SEARCH_KEYS


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_execute_cmd(command: str) -> str:
    """
    Return a *similar-command key* for retry-spiral detection.
    """
    cleaned = re.sub(r"['\"`]", "", (command or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    tokens = cleaned.split(" ")
    flags = [t for t in tokens if t.startswith("-")]
    parts = [t for t in tokens if not t.startswith("-")]
    if not parts:
        return cleaned

    verb = parts[0]

    # ── python / python3 / py / python3.x ────────────────────────────────────
    if re.match(r"^py(thon[0-9.]*)?$", verb):
        norm_verb = "python"
        if flags:
            first_flag = flags[0]
            if first_flag in {"-v", "-version", "--version"}:
                return f"{norm_verb} --version"
            if first_flag == "-c":
                snippet = " ".join(p for p in parts[1:] if p)[:60]
                return f"{norm_verb} -c {snippet}"
            if first_flag == "-m" and len(parts) > 1:
                sub = parts[1]
                if sub in {"pip", "pip3"} and len(parts) > 2:
                    sub_action = parts[2]
                    return f"{norm_verb} -m {sub} {sub_action}"
                return f"{norm_verb} -m {sub}"
            return f"{norm_verb} {first_flag}"
        if len(parts) > 1:
            return f"{norm_verb} {parts[1]}"
        return norm_verb

    # ── pip / pip3 ────────────────────────────────────────────────────────────
    if verb in {"pip", "pip3"}:
        norm_verb = "pip"
        if len(parts) > 1:
            return f"{norm_verb} {parts[1]}"
        if flags:
            return f"{norm_verb} {flags[0]}"
        return norm_verb

    # ── where / Get-Command ───────────────────────────────────────────────────
    if verb in {"where", "get-command", "which"}:
        target = parts[1] if len(parts) > 1 else ""
        if re.match(r"^py(thon[0-9.]*)?$", target):
            target = "python"
        return f"{verb} {target}"

    # ── Start-Process / Stop-Process ──────────────────────────────────────────
    if verb in {"start-process", "stop-process"} and len(parts) > 1:
        target = re.sub(r"\.exe$", "", parts[1])
        return f"{verb} {target}"

    # ── Get-Content ───────────────────────────────────────────────────────────
    if verb == "get-content":
        path = _extract_primary_path(parts)
        tail = " -tail" if "-tail" in flags else ""
        return f"get-content {path}{tail}"

    # ── Get-ChildItem ─────────────────────────────────────────────────────────
    if verb == "get-childitem":
        path = _extract_primary_path(parts)
        return f"get-childitem {path}"

    # ── Generic fallback ──────────────────────────────────────────────────────
    return " ".join(parts[:2])


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_ENV_DISCOVERY_KEY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^python --version$"),
    re.compile(r"^python -v$"),
    re.compile(r"^python -m pip --version$"),
    re.compile(r"^python -m pip$"),
    re.compile(r"^python -m venv"),
    re.compile(r"^python -c import "),
    re.compile(r"^pip --version$"),
    re.compile(r"^pip -v$"),
    re.compile(r"^where python"),
    re.compile(r"^get-command python"),
    re.compile(r"^which python"),
)


def classify_cmd(command: str) -> CmdCategory:
    key = normalize_execute_cmd(command)
    if not key:
        return CmdCategory.UNKNOWN

    verb = key.split(" ")[0]

    for pattern in _ENV_DISCOVERY_KEY_PATTERNS:
        if pattern.match(key):
            return CmdCategory.ENVIRONMENT_DISCOVERY

    if verb in _READ_ONLY_VERBS:
        return CmdCategory.READ_ONLY_INSPECTION

    if verb in _INSTALL_VERBS:
        action = key.split(" ")[1] if len(key.split(" ")) > 1 else ""
        if action == "install":
            return CmdCategory.PACKAGE_INSTALL
        return CmdCategory.ENVIRONMENT_DISCOVERY

    if "python" == verb and " -m pip install" in key:
        return CmdCategory.PACKAGE_INSTALL

    if verb in _DESTRUCTIVE_VERBS:
        return CmdCategory.DESTRUCTIVE

    if verb in _PROCESS_LAUNCH_VERBS:
        return CmdCategory.PROCESS_LAUNCH

    return CmdCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Per-category config
# ---------------------------------------------------------------------------

_CATEGORY_SIMILAR_LIMIT: dict[CmdCategory, int] = {
    CmdCategory.ENVIRONMENT_DISCOVERY: MAX_SIMILAR_ENVIRONMENT_DISCOVERY,
    CmdCategory.READ_ONLY_INSPECTION:  MAX_SIMILAR_READ_ONLY,
    CmdCategory.PACKAGE_INSTALL:       MAX_SIMILAR_SETUP,
    CmdCategory.PROCESS_LAUNCH:        MAX_SIMILAR_PROCESS_LAUNCH,
    CmdCategory.DESTRUCTIVE:           MAX_SIMILAR_DESTRUCTIVE,
    CmdCategory.UNKNOWN:               MAX_SIMILAR_UNKNOWN,
}

_NON_MUTATING_CATEGORIES: frozenset[CmdCategory] = frozenset({
    CmdCategory.ENVIRONMENT_DISCOVERY,
    CmdCategory.READ_ONLY_INSPECTION,
})


# ---------------------------------------------------------------------------
# Public helpers (backwards-compat)
# ---------------------------------------------------------------------------

def _is_readonly_cmd(command_key: str) -> bool:
    verb = command_key.split(" ")[0] if command_key else ""
    return verb in _READ_ONLY_VERBS


def _is_install_check_cmd(command_key: str) -> bool:
    verb = command_key.split(" ")[0] if command_key else ""
    return verb in _INSTALL_VERBS


def budget_guard_entry(error: str) -> dict:
    return {
        "action": "budget_guard",
        "command": "[budget_guard]",
        "device_id": None,
        "result": {"error": error},
    }


# ---------------------------------------------------------------------------
# EnvDiscoveryGuard
# ---------------------------------------------------------------------------

class EnvDiscoveryGuard:
    """
    Stateful guard that tracks Python-environment discovery within one task.

    Rules:
    1. interpreter_found: set to True when the controller explicitly calls
       mark_interpreter_found() after a successful python --version execution.
       Once set, further interpreter-search commands count toward
       post_found_searches. If that count exceeds MAX_POST_FOUND_INTERPRETER_SEARCHES
       the guard returns DEPENDENCY_MISSING_ERROR.

    2. failed_import_checks: records packages whose import-check failed
       (ModuleNotFoundError). If the same package import is attempted again
       after being recorded as failed, the guard returns DEPENDENCY_MISSING_ERROR.
       Same-package retries > MAX_REPEATED_IMPORT_CHECKS are also blocked.

    3. Missing package is NOT classified as "Python not found".

    The guard is query-only — it never installs packages.
    """

    def __init__(
        self,
        max_post_found_searches: int = MAX_POST_FOUND_INTERPRETER_SEARCHES,
        max_repeated_import_checks: int = MAX_REPEATED_IMPORT_CHECKS,
    ) -> None:
        self.max_post_found_searches = max_post_found_searches
        self.max_repeated_import_checks = max_repeated_import_checks

        self.interpreter_found: bool = False
        self.post_found_searches: int = 0
        # package_name -> number of times import-check was attempted after first failure
        self.failed_import_retries: dict[str, int] = {}

    # ------------------------------------------------------------------
    def mark_interpreter_found(self) -> None:
        """Call this when python --version (or equivalent) exits with code 0."""
        self.interpreter_found = True

    def record_import_failure(self, package: str) -> None:
        """
        Call this when an import-check command produced output containing
        'ModuleNotFoundError' or 'No module named'.
        """
        pkg = package.lower()
        self.failed_import_retries.setdefault(pkg, 0)

    def check_command(self, command: str) -> str | None:
        """
        Called *before* executing a command.
        Returns DEPENDENCY_MISSING_ERROR if the command should be blocked,
        else None.
        """
        key = normalize_execute_cmd(command)
        pkg = _extract_import_package(command)

        # ── Rule 1: interpreter search after interpreter already found ────────
        if self.interpreter_found and _is_interpreter_search_cmd(key):
            self.post_found_searches += 1
            if self.post_found_searches > self.max_post_found_searches:
                if _debug_enabled():
                    _log.debug(
                        "[env_discovery BLOCK] interpreter already found, "
                        "blocking interpreter search: key=%r post_found=%d/%d",
                        key, self.post_found_searches, self.max_post_found_searches,
                    )
                return DEPENDENCY_MISSING_ERROR

        # ── Rule 2: repeated import-check for a known-failed package ──────────
        if pkg is not None and pkg in self.failed_import_retries:
            self.failed_import_retries[pkg] += 1
            if self.failed_import_retries[pkg] > self.max_repeated_import_checks:
                if _debug_enabled():
                    _log.debug(
                        "[env_discovery BLOCK] repeated import-check for failed package %r "
                        "attempt=%d/%d",
                        pkg,
                        self.failed_import_retries[pkg],
                        self.max_repeated_import_checks,
                    )
                return DEPENDENCY_MISSING_ERROR

        return None


# ---------------------------------------------------------------------------
# CommandBudget
# ---------------------------------------------------------------------------

class CommandBudget:
    """
    Tracks tool / command usage and returns an error string when a budget
    is exceeded, signalling that the agent should stop.

    Includes an EnvDiscoveryGuard to stop Python-environment spiral.
    Enable IRU_DEBUG_BUDGET=1 to see per-call budget state in logs.
    """

    def __init__(
        self,
        *,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_TASK,
        max_execute_cmd_calls: int = MAX_MUTATING_CMD_CALLS,
        max_similar_execute_cmd_calls: int = MAX_SIMILAR_UNKNOWN,
        max_similar_readonly_calls: int = MAX_SIMILAR_READ_ONLY,
        max_similar_install_calls: int = MAX_SIMILAR_SETUP,
        max_mutating_cmd_calls: int | None = None,
        max_environment_discovery_calls: int = MAX_ENVIRONMENT_DISCOVERY_CALLS,
        max_read_only_cmd_calls: int = MAX_READ_ONLY_CMD_CALLS,
        max_similar_environment_discovery: int = MAX_SIMILAR_ENVIRONMENT_DISCOVERY,
        max_post_found_interpreter_searches: int = MAX_POST_FOUND_INTERPRETER_SEARCHES,
        max_repeated_import_checks: int = MAX_REPEATED_IMPORT_CHECKS,
    ) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_mutating_cmd_calls = (
            max_mutating_cmd_calls
            if max_mutating_cmd_calls is not None
            else max_execute_cmd_calls
        )
        self.max_environment_discovery_calls = max_environment_discovery_calls
        self.max_read_only_cmd_calls = max_read_only_cmd_calls

        self._similar_limits: dict[CmdCategory, int] = dict(_CATEGORY_SIMILAR_LIMIT)
        self._similar_limits[CmdCategory.READ_ONLY_INSPECTION] = max_similar_readonly_calls
        self._similar_limits[CmdCategory.PACKAGE_INSTALL] = max_similar_install_calls
        self._similar_limits[CmdCategory.UNKNOWN] = max_similar_execute_cmd_calls
        self._similar_limits[CmdCategory.ENVIRONMENT_DISCOVERY] = max_similar_environment_discovery

        # Counters
        self.tool_calls_count = 0
        self.mutating_cmd_count = 0
        self.environment_discovery_count = 0
        self.read_only_cmd_count = 0
        self.cmd_key_counts: dict[str, int] = {}

        # EnvDiscoveryGuard — shared instance, injectable for tests
        self.env_guard = EnvDiscoveryGuard(
            max_post_found_searches=max_post_found_interpreter_searches,
            max_repeated_import_checks=max_repeated_import_checks,
        )

        # Legacy aliases
        self.execute_cmd_count = 0
        self.execute_cmd_prefix_counts = self.cmd_key_counts

    # ------------------------------------------------------------------
    def mark_interpreter_found(self) -> None:
        """Proxy to env_guard — call after a successful python --version."""
        self.env_guard.mark_interpreter_found()

    def record_import_failure(self, package: str) -> None:
        """Proxy to env_guard — call when import-check output contains ModuleNotFoundError."""
        self.env_guard.record_import_failure(package)

    # ------------------------------------------------------------------
    def register(self, fn_name: str, command: str = "") -> str | None:
        """
        Register a tool call.
        Returns an error string if any budget is exceeded, else None.
        """
        debug = _debug_enabled()

        self.tool_calls_count += 1
        if self.tool_calls_count > self.max_tool_calls:
            reason = f"tool_calls {self.tool_calls_count}/{self.max_tool_calls} exceeded"
            if debug:
                _log.debug("[budget_guard BLOCK] fn=%s | reason: %s", fn_name, reason)
            return BUDGET_GUARD_ERROR

        if fn_name != "execute_cmd":
            if debug:
                _log.debug(
                    "[budget] fn=%s | tool_calls=%d/%d | PASS (non-execute_cmd)",
                    fn_name, self.tool_calls_count, self.max_tool_calls,
                )
            return None

        # ── EnvDiscoveryGuard check (before counting) ─────────────────────────
        env_block = self.env_guard.check_command(command)
        if env_block:
            if debug:
                _log.debug(
                    "[budget BLOCK] fn=execute_cmd | cmd=%.120s | env_guard blocked",
                    (command or "").replace("\n", " "),
                )
            return env_block

        # execute_cmd path
        self.execute_cmd_count += 1

        category = classify_cmd(command)
        cmd_key  = normalize_execute_cmd(command)

        # ── Per-category hard-cap ─────────────────────────────────────────────
        block_reason: str | None = None

        if category == CmdCategory.ENVIRONMENT_DISCOVERY:
            self.environment_discovery_count += 1
            if self.environment_discovery_count > self.max_environment_discovery_calls:
                block_reason = (
                    f"env_discovery_count {self.environment_discovery_count}"
                    f"/{self.max_environment_discovery_calls} exceeded"
                )

        elif category == CmdCategory.READ_ONLY_INSPECTION:
            self.read_only_cmd_count += 1
            if self.read_only_cmd_count > self.max_read_only_cmd_calls:
                block_reason = (
                    f"read_only_count {self.read_only_cmd_count}"
                    f"/{self.max_read_only_cmd_calls} exceeded"
                )

        else:
            self.mutating_cmd_count += 1
            if self.mutating_cmd_count > self.max_mutating_cmd_calls:
                block_reason = (
                    f"mutating_count {self.mutating_cmd_count}"
                    f"/{self.max_mutating_cmd_calls} exceeded"
                )

        # ── Per-key similarity check ──────────────────────────────────────────
        same_key_count = 0
        similar_limit = self._similar_limits[category]
        if cmd_key and block_reason is None:
            self.cmd_key_counts[cmd_key] = self.cmd_key_counts.get(cmd_key, 0) + 1
            same_key_count = self.cmd_key_counts[cmd_key]
            if same_key_count > similar_limit:
                block_reason = (
                    f"same_key_count {same_key_count}/{similar_limit} exceeded"
                    f" (key={cmd_key!r})"
                )
        elif cmd_key:
            self.cmd_key_counts[cmd_key] = self.cmd_key_counts.get(cmd_key, 0) + 1
            same_key_count = self.cmd_key_counts[cmd_key]

        # ── Debug log ─────────────────────────────────────────────────────────
        if debug:
            status = "BLOCK" if block_reason else "pass"
            if category == CmdCategory.ENVIRONMENT_DISCOVERY:
                cat_counter = f"env_discovery={self.environment_discovery_count}/{self.max_environment_discovery_calls}"
            elif category == CmdCategory.READ_ONLY_INSPECTION:
                cat_counter = f"read_only={self.read_only_cmd_count}/{self.max_read_only_cmd_calls}"
            else:
                cat_counter = f"mutating={self.mutating_cmd_count}/{self.max_mutating_cmd_calls}"

            _log.debug(
                "[budget %s] fn=execute_cmd | cmd=%.120s"
                " | key=%r | category=%s"
                " | tool_calls=%d/%d | execute_cmd=%d"
                " | %s"
                " | same_key=%d/%d"
                " | block_reason=%s",
                status,
                (command or "").replace("\n", " "),
                cmd_key,
                category.value,
                self.tool_calls_count,
                self.max_tool_calls,
                self.execute_cmd_count,
                cat_counter,
                same_key_count,
                similar_limit,
                block_reason or "none",
            )

        if block_reason:
            return BUDGET_GUARD_ERROR
        return None

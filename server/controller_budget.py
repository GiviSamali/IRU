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
"""
from __future__ import annotations

import re
from enum import Enum


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

# Legacy aliases kept so existing code / tests that import them still work
MAX_EXECUTE_CMD_CALLS_PER_TASK = MAX_MUTATING_CMD_CALLS
MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK = MAX_SIMILAR_UNKNOWN
MAX_SIMILAR_READONLY_CALLS_PER_TASK = MAX_SIMILAR_READ_ONLY
MAX_SIMILAR_INSTALL_CALLS_PER_TASK = MAX_SIMILAR_SETUP

BUDGET_GUARD_ERROR = (
    "Я остановился: было выполнено несколько похожих попыток, но надёжно подтвердить результат не удалось. "
    "Чтобы не выполнять лишние команды, продолжение остановлено."
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_primary_path(parts: list[str]) -> str:
    """Return the first token that looks like a filesystem path."""
    for p in parts:
        if re.search(r"[/\\]", p) or re.match(r"[a-zA-Z]:", p):
            return p.lower()
    return parts[1].lower() if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_execute_cmd(command: str) -> str:
    """
    Return a *similar-command key* for retry-spiral detection.

    Keys are designed so that:
    - Semantically different commands get different keys.
    - Variants of the same intent ("Start-Process calc" / "calc.exe") collapse.
    - Environment-discovery variants stay distinct to avoid false positives.
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
        norm_verb = "python"  # collapse python3 / py / python3.11 to "python"
        if flags:
            first_flag = flags[0]
            if first_flag in {"-v", "-version", "--version"}:
                return f"{norm_verb} --version"
            if first_flag == "-c":
                # Distinguish by the actual import/call – first module name is enough
                snippet = " ".join(p for p in parts[1:] if p)[:60]
                return f"{norm_verb} -c {snippet}"
            if first_flag == "-m" and len(parts) > 1:
                sub = parts[1]  # e.g. "pip"
                # python -m pip --version  vs  python -m pip install  → different
                if sub in {"pip", "pip3"} and len(parts) > 2:
                    sub_action = parts[2]  # "install" / "--version" / "list" …
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
            action = parts[1]  # "install" / "--version" / "list" …
            return f"{norm_verb} {action}"
        if flags:
            return f"{norm_verb} {flags[0]}"
        return norm_verb

    # ── where / Get-Command ───────────────────────────────────────────────────
    if verb in {"where", "get-command", "which"}:
        target = parts[1] if len(parts) > 1 else ""
        # Collapse python / python3 / py  targets to a single key
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

# Environment-discovery keys are matched by prefix pattern after normalisation.
_ENV_DISCOVERY_KEY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^python --version$"),
    re.compile(r"^python -v$"),
    re.compile(r"^python -m pip --version$"),
    re.compile(r"^python -m pip$"),           # bare  python -m pip
    re.compile(r"^python -m venv"),
    re.compile(r"^python -c import "),         # import sys / import site …
    re.compile(r"^pip --version$"),
    re.compile(r"^pip -v$"),
    re.compile(r"^where python"),
    re.compile(r"^get-command python"),
    re.compile(r"^which python"),
)


def classify_cmd(command: str) -> CmdCategory:
    """
    Classify a raw command string into a CmdCategory.

    Classification is based on the *normalised key* produced by
    normalize_execute_cmd() so it is consistent with spiral detection.
    """
    key = normalize_execute_cmd(command)
    if not key:
        return CmdCategory.UNKNOWN

    verb = key.split(" ")[0]

    # Environment discovery check (by key pattern)
    for pattern in _ENV_DISCOVERY_KEY_PATTERNS:
        if pattern.match(key):
            return CmdCategory.ENVIRONMENT_DISCOVERY

    # Read-only inspection
    if verb in _READ_ONLY_VERBS:
        return CmdCategory.READ_ONLY_INSPECTION

    # Package install / setup  (pip install, python -m pip install, …)
    if verb in _INSTALL_VERBS:
        action = key.split(" ")[1] if len(key.split(" ")) > 1 else ""
        if action == "install":
            return CmdCategory.PACKAGE_INSTALL
        # pip --version / pip list → environment discovery
        return CmdCategory.ENVIRONMENT_DISCOVERY

    if "python" == verb and " -m pip install" in key:
        return CmdCategory.PACKAGE_INSTALL

    # Destructive
    if verb in _DESTRUCTIVE_VERBS:
        return CmdCategory.DESTRUCTIVE

    # Process launch
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

# Categories that do NOT consume the mutating execute_cmd budget
_NON_MUTATING_CATEGORIES: frozenset[CmdCategory] = frozenset({
    CmdCategory.ENVIRONMENT_DISCOVERY,
    CmdCategory.READ_ONLY_INSPECTION,
})


# ---------------------------------------------------------------------------
# Public helpers (kept for backwards-compat with existing tests)
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
# CommandBudget
# ---------------------------------------------------------------------------

class CommandBudget:
    """
    Tracks tool / command usage and returns an error string when a budget
    is exceeded, signalling that the agent should stop.

    Counters:
      tool_calls_count          – all tool calls (hard cap)
      mutating_cmd_count        – mutating execute_cmd calls
      environment_discovery_count – env-discovery execute_cmd calls
      read_only_cmd_count       – read-only execute_cmd calls
      cmd_key_counts            – per-key counter for spiral detection
    """

    def __init__(
        self,
        *,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_TASK,
        max_execute_cmd_calls: int = MAX_MUTATING_CMD_CALLS,
        max_similar_execute_cmd_calls: int = MAX_SIMILAR_UNKNOWN,
        max_similar_readonly_calls: int = MAX_SIMILAR_READ_ONLY,
        max_similar_install_calls: int = MAX_SIMILAR_SETUP,
        # New parameters (can still be overridden in tests)
        max_mutating_cmd_calls: int | None = None,
        max_environment_discovery_calls: int = MAX_ENVIRONMENT_DISCOVERY_CALLS,
        max_read_only_cmd_calls: int = MAX_READ_ONLY_CMD_CALLS,
        max_similar_environment_discovery: int = MAX_SIMILAR_ENVIRONMENT_DISCOVERY,
    ) -> None:
        # Legacy param alias
        self.max_tool_calls = max_tool_calls
        self.max_mutating_cmd_calls = (
            max_mutating_cmd_calls
            if max_mutating_cmd_calls is not None
            else max_execute_cmd_calls
        )
        self.max_environment_discovery_calls = max_environment_discovery_calls
        self.max_read_only_cmd_calls = max_read_only_cmd_calls

        # Per-category similar-limits (allow test overrides via legacy params)
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

        # Legacy alias
        self.execute_cmd_count = 0
        self.execute_cmd_prefix_counts = self.cmd_key_counts

    # ------------------------------------------------------------------
    def register(self, fn_name: str, command: str = "") -> str | None:
        """
        Register a tool call. Returns BUDGET_GUARD_ERROR if any budget is
        exceeded, otherwise returns None.
        """
        self.tool_calls_count += 1
        if self.tool_calls_count > self.max_tool_calls:
            return BUDGET_GUARD_ERROR

        if fn_name != "execute_cmd":
            return None

        # Legacy counter (equals mutating+env+readonly combined)
        self.execute_cmd_count += 1

        category = classify_cmd(command)
        cmd_key  = normalize_execute_cmd(command)

        # ── Per-category hard-cap check ───────────────────────────────────
        if category == CmdCategory.ENVIRONMENT_DISCOVERY:
            self.environment_discovery_count += 1
            if self.environment_discovery_count > self.max_environment_discovery_calls:
                return BUDGET_GUARD_ERROR

        elif category == CmdCategory.READ_ONLY_INSPECTION:
            self.read_only_cmd_count += 1
            if self.read_only_cmd_count > self.max_read_only_cmd_calls:
                return BUDGET_GUARD_ERROR

        else:
            # All mutating categories count toward the mutating budget
            self.mutating_cmd_count += 1
            if self.mutating_cmd_count > self.max_mutating_cmd_calls:
                return BUDGET_GUARD_ERROR

        # ── Per-key similarity check (spiral detection) ───────────────────
        if cmd_key:
            self.cmd_key_counts[cmd_key] = self.cmd_key_counts.get(cmd_key, 0) + 1
            limit = self._similar_limits[category]
            if self.cmd_key_counts[cmd_key] > limit:
                return BUDGET_GUARD_ERROR

        return None

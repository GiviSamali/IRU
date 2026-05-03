import re

MAX_TOOL_CALLS_PER_TASK = 12
MAX_EXECUTE_CMD_CALLS_PER_TASK = 10
MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK = 3

# Read-only inspection commands that should not be treated as retry spirals.
# They are safe (no side effects) and naturally appear in multi-step sequences.
READONLY_INSPECTION_CMDS = {
    "get-childitem",
    "get-content",
    "test-path",
    "resolve-path",
    "select-string",
    "get-process",
    "get-service",
    # Unix / cmd equivalents
    "dir",
    "ls",
    "cat",
    "type",
    "grep",
    "findstr",
}

# Read-only commands may repeat more times before being flagged as a spiral.
MAX_SIMILAR_READONLY_CALLS_PER_TASK = 6

# Install/check commands: pip install, python -m pip, python --version, py --version
# These are diagnostic/setup and should not be counted as a retry spiral.
INSTALL_CHECK_CMDS = {
    "pip",
    "pip3",
}

# How many install/check commands are allowed before flagging as spiral.
# (e.g. pip install X fails -> pip install X --upgrade -> still same command key)
MAX_SIMILAR_INSTALL_CALLS_PER_TASK = 4

BUDGET_GUARD_ERROR = (
    "Я остановился: было выполнено несколько похожих попыток, но надёжно подтвердить результат не удалось. "
    "Чтобы не выполнять лишние команды, продолжение остановлено."
)


def _extract_primary_path(parts: list[str]) -> str:
    """Return the first argument that looks like a file-system path."""
    for p in parts:
        if re.search(r"[/\\]", p) or re.match(r"[a-zA-Z]:", p):
            return p.lower()
    # Fall back to first non-flag token after the command name
    if len(parts) > 1:
        return parts[1].lower()
    return ""


def normalize_execute_cmd(command: str) -> str:
    """
    Build a *similar-command key* used to detect retry spirals.

    Key design rules:
    - Get-ChildItem vs Get-Content → different (different verb)
    - Get-Content full read vs Get-Content -Tail → different (tail flag included)
    - Different primary paths → different keys
    - Start-Process calc.exe / calc / "calc.exe" → same key (retry spiral detection)
    - python --version vs python -c vs python -m → different keys (different sub-verb/flag)
    """
    normalized = re.sub(r"['\"`]", "", (command or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""

    tokens = normalized.split(" ")
    # Separate flags (start with "-") from positional args
    flags = [t for t in tokens if t.startswith("-")]
    parts = [t for t in tokens if not t.startswith("-")]

    if not parts:
        return normalized

    verb = parts[0]

    # ── python / python3 / py ───────────────────────────────────────────────
    # Distinguish: python --version / python -c "code" / python -m module / python script.py
    # The first flag or second positional becomes the differentiator.
    if verb in {"python", "python3", "py"}:
        # Capture the distinguishing first flag or positional arg
        if flags:
            first_flag = flags[0]  # e.g. --version, -c, -m
            if first_flag in {"--version", "-version", "-v"}:
                return f"{verb} --version"
            if first_flag == "-c":
                # Each -c call is a different script snippet; use full normalized form
                # but cap at a reasonable length to avoid explosion
                return f"{verb} -c {normalized[len(verb) + 3:][:60]}"
            if first_flag == "-m" and len(parts) > 1:
                return f"{verb} -m {parts[1]}"
            return f"{verb} {first_flag}"
        # No flags: python script.py
        if len(parts) > 1:
            return f"{verb} {parts[1]}"
        return verb

    # ── Start-Process / Stop-Process ─────────────────────────────────────────────
    # Normalise the target executable name so variants collapse to one key.
    if verb in {"start-process", "stop-process"} and len(parts) > 1:
        target = re.sub(r"\.exe$", "", parts[1])
        return f"{verb} {target}"

    # ── Get-Content ─────────────────────────────────────────────────────────────
    # Distinguish full-read from tail-read and track the actual path.
    if verb == "get-content":
        path = _extract_primary_path(parts)
        tail_flag = "-tail" in flags
        suffix = " -tail" if tail_flag else ""
        return f"get-content {path}{suffix}"

    # ── Get-ChildItem ───────────────────────────────────────────────────────────
    # The root search path is the differentiator; include it in the key.
    if verb == "get-childitem":
        path = _extract_primary_path(parts)
        return f"get-childitem {path}"

    # ── Generic fallback ────────────────────────────────────────────────────────
    # Use verb + first positional arg (same as before).
    return " ".join(parts[:2])


def _is_readonly_cmd(command_key: str) -> bool:
    """Return True when the command key belongs to a read-only inspection verb."""
    verb = command_key.split(" ")[0] if command_key else ""
    return verb in READONLY_INSPECTION_CMDS


def _is_install_check_cmd(command_key: str) -> bool:
    """Return True when the command key belongs to a package install/check verb."""
    verb = command_key.split(" ")[0] if command_key else ""
    return verb in INSTALL_CHECK_CMDS


def budget_guard_entry(error: str) -> dict:
    return {
        "action": "budget_guard",
        "command": "[budget_guard]",
        "device_id": None,
        "result": {"error": error},
    }


class CommandBudget:
    def __init__(
        self,
        *,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_TASK,
        max_execute_cmd_calls: int = MAX_EXECUTE_CMD_CALLS_PER_TASK,
        max_similar_execute_cmd_calls: int = MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK,
        max_similar_readonly_calls: int = MAX_SIMILAR_READONLY_CALLS_PER_TASK,
        max_similar_install_calls: int = MAX_SIMILAR_INSTALL_CALLS_PER_TASK,
    ) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_execute_cmd_calls = max_execute_cmd_calls
        self.max_similar_execute_cmd_calls = max_similar_execute_cmd_calls
        self.max_similar_readonly_calls = max_similar_readonly_calls
        self.max_similar_install_calls = max_similar_install_calls
        self.tool_calls_count = 0
        self.execute_cmd_count = 0
        self.execute_cmd_prefix_counts: dict[str, int] = {}

    def register(self, fn_name: str, command: str = "") -> str | None:
        self.tool_calls_count += 1
        if self.tool_calls_count > self.max_tool_calls:
            return BUDGET_GUARD_ERROR

        if fn_name != "execute_cmd":
            return None

        self.execute_cmd_count += 1
        if self.execute_cmd_count > self.max_execute_cmd_calls:
            return BUDGET_GUARD_ERROR

        command_key = normalize_execute_cmd(command)
        self.execute_cmd_prefix_counts[command_key] = (
            self.execute_cmd_prefix_counts.get(command_key, 0) + 1
        )

        if command_key:
            if _is_readonly_cmd(command_key):
                limit = self.max_similar_readonly_calls
            elif _is_install_check_cmd(command_key):
                limit = self.max_similar_install_calls
            else:
                limit = self.max_similar_execute_cmd_calls
            if self.execute_cmd_prefix_counts[command_key] > limit:
                return BUDGET_GUARD_ERROR

        return None

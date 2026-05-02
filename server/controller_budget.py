import re


MAX_TOOL_CALLS_PER_TASK = 6
MAX_EXECUTE_CMD_CALLS_PER_TASK = 5
MAX_SIMILAR_EXECUTE_CMD_CALLS_PER_TASK = 3
BUDGET_GUARD_ERROR = (
    "Я остановился: было выполнено несколько похожих попыток, но надёжно подтвердить результат не удалось. "
    "Чтобы не выполнять лишние команды, продолжение остановлено."
)


def normalize_execute_cmd(command: str) -> str:
    normalized = re.sub(r"['\"`]", "", (command or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""

    parts = [part for part in normalized.split(" ") if not part.startswith("-")]
    if not parts:
        return normalized

    head = parts[0]
    if head in {"start-process", "get-process", "stop-process"} and len(parts) > 1:
        target = re.sub(r"\.exe$", "", parts[1])
        return f"{head} {target}"
    return " ".join(parts[:2])


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
    ) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_execute_cmd_calls = max_execute_cmd_calls
        self.max_similar_execute_cmd_calls = max_similar_execute_cmd_calls
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

        command_prefix = normalize_execute_cmd(command)
        self.execute_cmd_prefix_counts[command_prefix] = self.execute_cmd_prefix_counts.get(command_prefix, 0) + 1
        if command_prefix and self.execute_cmd_prefix_counts[command_prefix] > self.max_similar_execute_cmd_calls:
            return BUDGET_GUARD_ERROR
        return None

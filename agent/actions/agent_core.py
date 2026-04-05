# agent_core.py
from typing import Any, Dict

from agent.actions.files import find_file  # подстрой import под свою структуру


def run_action(device_id: str, action: str, params: Dict[str, Any]) -> Any:
    # пока device_id просто игнорируем или логируем
    if action == "find_file":
        return find_file(
            name_part=params.get("name_part", ""),
            base=params.get("base"),
            max_results=params.get("max_results", 20),
        )

    # сюда будешь добавлять новые действия
    raise ValueError(f"Unknown action: {action}")

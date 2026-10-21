"""One user-facing Goal projection from owner-scoped, durable Worker Reports.

Worker prose and failed intermediate tool calls are not a conversation message.
Only the scheduler's frozen task outcomes and evidence-bearing artifacts can
authorize a success claim. No LLM call, new permission or filesystem mutation.
"""
from __future__ import annotations

from pathlib import PureWindowsPath

TERMINAL = frozenset({"success", "partial", "failed", "blocked", "unknown", "cancelled"})
ACTIVE = frozenset({"running", "queued", "waiting_confirmation", "waiting_input"})


def goal_presentation(goal: dict) -> dict:
    """A deterministic presentation, not an additional evaluator of Worker prose."""
    members = [m for m in goal.get("tasks") or [] if m.get("required_for_goal", True)]
    finished = [m for m in members if m.get("status") == "success"
                and (m.get("report") or {}).get("goal_completed") is True
                and m.get("result")]
    done = len(finished)
    total = len(members)
    by_id = {m.get("task_id"): m for m in members}
    dependent_blocked = []
    for member in members:
        if member.get("status") != "waiting_dependency":
            continue
        sources = [by_id.get(edge.get("source_task_id")) for edge in member.get("dependencies") or []]
        if any(source and source.get("status") in TERMINAL - {"success"} for source in sources):
            dependent_blocked.append(member.get("task_id"))
    waiting = [m for m in members if m.get("status") == "waiting_dependency"
               and m.get("task_id") not in dependent_blocked]
    running = any(m.get("status") in ACTIVE for m in members) or bool(waiting)
    terminal = not running and (all(m.get("status") in TERMINAL for m in members)
                                or len(dependent_blocked) + sum(m.get("status") in TERMINAL for m in members) == total)
    success = bool(total and goal.get("goal_completed") is True and done == total)
    if success:
        status = "success"
    elif not terminal:
        status = "running"
    elif any(m.get("status") == "unknown" for m in members):
        status = "unknown"
    elif done or any(m.get("status") == "partial" for m in members):
        status = "partial"
    elif all(m.get("status") == "cancelled" for m in members):
        status = "cancelled"
    else:
        status = "failed"

    artifacts = []
    shown = []
    seen = set()
    for member in finished:
        report = member.get("report") or {}
        expected = [str(item).casefold().replace("\\", "/")
                    for item in member.get("expected_artifacts") or [] if isinstance(item, str)]
        for file in report.get("artifacts") or []:
            if not isinstance(file, dict) or file.get("verified") is not True:
                continue
            path = file.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            ident = (file.get("device_id"), path.casefold())
            if ident in seen:
                continue
            seen.add(ident)
            name = file.get("name") or PureWindowsPath(path).name
            artifact = {"path":path, "name":name, "device_id":file.get("device_id"),
                        "task_id":member.get("task_id"),"verified":True}
            artifacts.append(artifact)
            # A helper generator (.py, scripts/helpers) is not the deliverable
            # unless the assignment explicitly identifies that file as output.
            canonical_path = path.casefold().replace("\\", "/")
            canonical_name = str(name).casefold()
            if expected:
                user_output = any(value == canonical_path or value == canonical_name
                                  or value.split("/")[-1] == canonical_name for value in expected)
            else:
                user_output = ("/scripts/helpers/" not in canonical_path
                               and not canonical_name.endswith((".py", ".ps1", ".sh", ".tmp")))
            if user_output and len(shown) < 8:
                shown.append(artifact)

    if success:
        message = ("Готово." if total == 1 else f"Готово. Подтверждены все {total} результата.")
    elif status == "running":
        message = f"Выполняю цель: подтверждено {done} из {total} результатов."
        attention = [m for m in members if m.get("status") in {"waiting_confirmation", "waiting_input"}]
        if attention:
            message += " Требуется решение в Операциях."
    elif status == "unknown":
        message = f"Исход цели не подтверждён. Подтверждено {done} из {total} результатов; автоматически не повторяю."
    elif status == "partial":
        message = f"Выполнено частично: подтверждено {done} из {total} результатов. Остальные требуют внимания."
    elif status == "cancelled":
        message = "Цель отменена."
    else:
        message = "Не удалось подтвердить выполнение цели."
    if terminal and shown:
        message += "\n" + "\n".join("Файл: " + a["path"] for a in shown)
    if terminal and not success:
        message += " Подробности по отдельным поручениям — в Операциях."

    return {"goal_id": goal.get("goal_id"), "status": status,
            "terminal": terminal, "goal_completed": success, "completed_count": done,
            "total_count": total, "message": message, "artifacts": artifacts,
            "requires_user_action": any(m.get("status") in {"waiting_confirmation", "waiting_input"}
                                       for m in members),
            "task_ids": [m.get("task_id") for m in members]}

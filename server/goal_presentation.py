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
    dependent_blocked = goal.get('dependent_blocked') or []
    terminal = goal.get('terminal', goal.get('status') in TERMINAL)
    status = goal.get('status','unknown')
    success = status == 'success' and goal.get('goal_completed') is True

    artifacts = []
    result_artifacts = []
    seen = set()
    # Verified files from partially completed Tasks remain visible as partial results.
    for member in members:
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
                        "task_id":member.get("task_id"),"verified":True,
                        **{key:file[key] for key in ("size_bytes","source_task_id","sha256","format","artifact_role","structure","verification_scope","content_verified") if key in file}}
            artifacts.append(artifact)
            # A helper generator (.py, scripts/helpers) is not the deliverable
            # unless the assignment explicitly identifies that file as output.
            canonical_path = path.casefold().replace("\\", "/")
            canonical_name = str(name).casefold()
            if expected:
                user_output = any(value == canonical_path or value == canonical_name
                                  or value.split("/")[-1] == canonical_name for value in expected)
                user_output = user_output or (file.get("artifact_role")=="final"
                    and file.get("format") in {"docx","xlsx","pptx"}
                    and file.get("source_task_id")==member.get("task_id")
                    and type(file.get("size_bytes")) is int and file["size_bytes"] > 0)
                user_output = user_output or (file.get("source_task_id") == member.get("task_id")
                    and type(file.get("size_bytes")) is int and file["size_bytes"] > 0
                    and "/scripts/helpers/" not in canonical_path
                    and not canonical_name.endswith((".py", ".ps1", ".sh", ".tmp")))
            else:
                user_output = ("/scripts/helpers/" not in canonical_path
                               and not canonical_name.endswith((".py", ".ps1", ".sh", ".tmp")))
            if user_output:
                result_artifacts.append(artifact)

    # Only Orchestrator composes conversation; this module renders its projection.
    try:
        from .orchestrator import present_goal_result
    except ImportError:
        from orchestrator import present_goal_result
    message = present_goal_result(goal)
    if terminal and result_artifacts:
        names = [a['name'] for a in result_artifacts]
        message += " Доступные файлы: " + ", ".join(names[:8]) + (" и другие." if len(names)>8 else ".")

    # Short, evidence-aligned voice notification: never read arbitrary paths or
    # a long technical report aloud. Full detail stays in Operations.
    spoken = message.split("\n", 1)[0].strip()
    if len(spoken) > 380 or "://" in spoken or "\\" in spoken or "\n" in spoken:
        spoken = {
            "success": "Работу закончил. Проверенный результат и подробности уже в чате.",
            "partial": "Закончил с частичным результатом. Часть действий подтверждена, остальное ещё не выполнено.",
            "unknown": "Работу остановил: подтвердить результат не удалось.",
            "failed": "Задачу завершить не удалось. Причина сохранена в отчёте.",
            "cancelled": "Задача отменена.",
        }.get(status, message.split("\n", 1)[0].strip())

    return {"goal_id": goal.get("goal_id"), "status": status,
            "terminal": terminal, "goal_completed": success, "completed_count": done,
            "spoken_message": spoken,
            "total_count": total, "message": message, "artifacts": artifacts,
            "result_artifacts": result_artifacts,
            "requires_user_action": any(m.get("status") in {"waiting_confirmation", "waiting_input"}
                                       for m in members),
            "task_ids": [m.get("task_id") for m in members]}

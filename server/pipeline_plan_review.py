"""User-driven PLAN review; execution waits without creating a second task."""
import asyncio
import uuid

try:
    from .runtime_state import tasks, is_task_cancel_requested
except ImportError:
    from runtime_state import tasks, is_task_cancel_requested


async def review_pipeline_plan(task_id, plan):
    # Direct/internal executions without a polling task have no interactive client.
    if task_id is None:
        return {"action": "approve"}
    task = tasks.get(task_id)
    if task is None:
        raise RuntimeError("PLAN task is no longer available")
    if is_task_cancel_requested(task_id):
        return {"action": "cancel"}
    decision = asyncio.get_running_loop().create_future()
    titles = [str(step["title"])[:90] for step in plan["steps"]]
    task["plan_review"] = {
        "revision": uuid.uuid4().hex,
        "goal": plan["goal"],
        "steps": plan["steps"],
        "speech": "Предлагаю такой план. " + " ".join(f"{i + 1}. {title}." for i, title in enumerate(titles))
                  + " Хотите что-то изменить?",
    }
    task["_pipeline_plan_future"] = decision
    task["confirm_data"] = {"kind": "plan_review"}
    task["current_step"] = "План готов. Ожидаю подтверждения или изменений."
    task["status"] = "confirm"
    try:
        result = await decision
        return {"action": "cancel"} if is_task_cancel_requested(task_id) else result
    finally:
        task.pop("_pipeline_plan_future", None)
        task.pop("plan_review", None)
        task.pop("confirm_data", None)

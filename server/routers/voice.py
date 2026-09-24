"""Authenticated voice output. The client selects a task, never arbitrary text."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

try:
    from .. import voice
    from ..api_support import _is_admin, check_rate_limit, get_current_user
    from ..database import get_user_plan, get_plan_trial_used
    from ..runtime_state import tasks
except ImportError:
    import voice
    from api_support import _is_admin, check_rate_limit, get_current_user
    from database import get_user_plan, get_plan_trial_used
    from runtime_state import tasks

router = APIRouter(prefix="/api/voice")
_active_users: set[int] = set()
TERMINAL_STATUSES = {"done", "error", "completed", "completed_with_recovery", "failed", "cancelled", "blocked"}


@router.get("/config")
async def config(request: Request):
    get_current_user(request)
    return {"available": voice.speech_configured(), "wake_word": "Иру",
            "wake_timeout_seconds": 10, "voice": "zahar"}


@router.post("/tasks/{task_id}/speech")
async def task_speech(task_id: str, request: Request, part: int = Query(0, ge=0), revision: str | None = None,
                      confirmation: str | None = None):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Задача не найдена")
    review = task.get("plan_review") if task.get("status") == "confirm" else None
    pending = task.get("confirm_data") or {}
    ordinary = (pending if task.get("status") == "confirm" and pending.get("kind") == "command" and pending.get("voice_allowed")
                and not review else None)
    if confirmation is not None and (not ordinary or ordinary.get("confirmation_id") != confirmation):
        raise HTTPException(409, "Подтверждение команды изменилось")
    if revision is not None and (not review or review.get("revision") != revision):
        raise HTTPException(409, "Вариант плана изменился")
    if task.get("status") not in TERMINAL_STATUSES and not review and not ordinary:
        raise HTTPException(409, "Задача ещё не завершена")
    plan_offer = bool(task.get("plan_suggestion") and not task.get("plan_declined") and not task.get("voice_plan_started"))
    offer_text = "Задача требует нескольких шагов. Предлагаю режим План: составлю план, выполню его и доложу результат. Запустить?"
    if plan_offer and not _is_admin(user) and get_user_plan(user["id"]) == "free" and get_plan_trial_used(user["id"]):
        offer_text = "Пробный запуск режима План уже использован. Для этого режима нужен тариф Про."
    parts = voice.answer_parts(ordinary["speech"] if ordinary else review["speech"] if review else offer_text if plan_offer else task.get("answer") or "", keep_inline=True)
    if not parts:
        return Response(status_code=204)
    if not voice.speech_configured():
        raise HTTPException(503, "Озвучка не настроена на сервере")
    user_id = user["id"]
    if user_id in _active_users or not check_rate_limit(f"voice:{user_id}"):
        raise HTTPException(429, "Озвучка занята. Попробуйте позже")
    _active_users.add(user_id)
    try:
        if not plan_offer and not review and not ordinary:
            parts = await voice.spoken_parts(task)
        if part >= len(parts):
            raise HTTPException(404, "Часть ответа не найдена")
        audio = await voice.synthesize(parts[part])
    except HTTPException:
        raise
    except Exception:
        # Provider responses can contain request text or credentials. Do not expose them.
        raise HTTPException(502, "Озвучка временно недоступна") from None
    finally:
        _active_users.discard(user_id)
    return Response(audio, media_type="audio/ogg", headers={
        "Cache-Control": "no-store", "X-Voice-Parts": str(len(parts)),
    })

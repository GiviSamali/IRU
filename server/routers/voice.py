"""Authenticated voice output. The client selects a task, never arbitrary text."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

try:
    from .. import voice
    from ..api_support import check_rate_limit, get_current_user
    from ..runtime_state import tasks
except ImportError:
    import voice
    from api_support import check_rate_limit, get_current_user
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
async def task_speech(task_id: str, request: Request, part: int = Query(0, ge=0)):
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Задача не найдена")
    if task.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(409, "Задача ещё не завершена")
    parts = voice.answer_parts(task.get("answer") or "")
    if not parts:
        return Response(status_code=204)
    if part >= len(parts):
        raise HTTPException(404, "Часть ответа не найдена")
    if not voice.speech_configured():
        raise HTTPException(503, "Озвучка не настроена на сервере")
    user_id = user["id"]
    if user_id in _active_users or not check_rate_limit(f"voice:{user_id}"):
        raise HTTPException(429, "Озвучка занята. Попробуйте позже")
    _active_users.add(user_id)
    try:
        audio = await voice.synthesize(parts[part])
    except Exception:
        # Provider responses can contain request text or credentials. Do not expose them.
        raise HTTPException(502, "Озвучка временно недоступна") from None
    finally:
        _active_users.discard(user_id)
    return Response(audio, media_type="audio/ogg", headers={
        "Cache-Control": "no-store", "X-Voice-Parts": str(len(parts)),
    })

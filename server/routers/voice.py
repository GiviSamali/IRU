import time
"""Authenticated voice output. The client selects a task, never arbitrary text."""
import base64
import io
import wave

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
TERMINAL_STATUSES = {"done", "error", "completed", "completed_with_recovery", "failed", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


@router.get("/config")
async def config(request: Request, provider: str = "yandex", device_id: str | None = None):
    user = get_current_user(request)
    if provider not in {"yandex", "piper", "elevenlabs"}:
        raise HTTPException(400, "Неизвестный обработчик озвучки")
    if provider == "piper":
        _piper_device(user["id"], device_id)
    if provider == "elevenlabs":
        available = voice.elevenlabs_configured()
    else:
        available = provider == "piper" or voice.speech_configured()
    return {"available": available, "wake_word": "Иру",
            "wake_timeout_seconds": 10, "voice": "elevenlabs" if provider == "elevenlabs" else "piper" if provider == "piper" else "zahar"}


def _piper_device(user_id: int, device_id: str | None) -> str:
    try:
        from ..runtime_state import devices, _dk
    except ImportError:
        from runtime_state import devices, _dk
    key = _dk(user_id, device_id or "")
    device = devices.get(key)
    if not device or device.get("user_id") != user_id:
        raise HTTPException(503, "Для Piper выберите подключённый компьютер")
    return key


@router.post("/tasks/{task_id}/speech")
async def task_speech(task_id: str, request: Request, part: int = Query(0, ge=0), revision: str | None = None,
                      confirmation: str | None = None, question: str | None = None, provider: str = "yandex", device_id: str | None = None):
    user = get_current_user(request)
    if provider not in {"yandex", "piper", "elevenlabs"}:
        raise HTTPException(400, "Неизвестный обработчик озвучки")
    task = tasks.get(task_id)
    if not task or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Задача не найдена")
    try:
        from ..window_policy import silent_window_success
        from ..browser_policy import silent_browser_success
    except ImportError:
        from window_policy import silent_window_success
        from browser_policy import silent_browser_success
    # A delegated Goal is projected from frozen Worker Reports, not from the
    # initiating Orchestrator turn (whose answer is intentionally empty).
    # Reading this endpoint cannot start, retry, or grant a Worker permission.
    goal_speech_task = None
    if task.get("kind") == "orchestrator" and task.get("result_first") and task.get("goal_id"):
        try:
            from ..worker_scheduler import goal_view
            from ..goal_presentation import goal_presentation
        except ImportError:
            from worker_scheduler import goal_view
            from goal_presentation import goal_presentation
        try:
            goal_result = goal_presentation(goal_view(task["goal_id"], user["id"], task["chat_id"]))
        except ValueError:
            raise HTTPException(404, "Результат цели недоступен") from None
        if not goal_result["terminal"]:
            raise HTTPException(409, "Задача ещё не завершена")
        spoken = goal_result["spoken_message"]
        goal_speech_task = {**task, "answer": spoken, "status": "done",
            "dialogue_intent": "conversation", "dialogue_spoken_response": spoken,
            "dialogue_speech_answer": spoken, "worker_report": None, "task_receipt": None}
    normalized = None
    if task.get("worker_id"):
        try:
            from ..response_presentation import normalized_worker_report
        except ImportError:
            from response_presentation import normalized_worker_report
        normalized = normalized_worker_report(task,task.get("worker_report"))
    silence_task = {**task,"status":"done"} if normalized and normalized["status"]=="success" else task
    if (normalized is None or normalized["status"]=="success") and (silent_window_success(silence_task) or silent_browser_success(silence_task)):
        return Response(status_code=204)
    if revision is not None:
        raise HTTPException(410, "execution_mode_unsupported")
    pending = task.get("confirm_data") or {}
    ordinary = (pending if task.get("status") == "confirm" and pending.get("kind") == "command" and pending.get("voice_allowed")
                and not task.get("plan_review") else None)
    if confirmation is not None and (not ordinary or ordinary.get("confirmation_id") != confirmation):
        raise HTTPException(409, "Подтверждение команды изменилось")
    input_event = task.get("input_required") or {}
    input_question = (task.get("status")=="waiting_input" and input_event.get("phase")=="waiting" and time.time()<input_event.get("expires_at",0))
    if question is not None and (not input_question or input_event.get("question_id") != question):
        raise HTTPException(409, "Вопрос уже изменился")
    if input_question and question is None:
        raise HTTPException(409, "Требуется идентификатор вопроса")
    if task.get("status") not in TERMINAL_STATUSES and not ordinary and not input_question:
        raise HTTPException(409, "Задача ещё не завершена")
    human_answer = goal_speech_task["answer"] if goal_speech_task else (task.get("answer") or "")
    # An empty written handoff can still have optional context-bound speech.
    if not goal_speech_task and task.get("kind")=="orchestrator" and task.get("dialogue_speech_answer")==task.get("answer"):
        human_answer = task.get("dialogue_spoken_response") or human_answer
    if task.get("worker_id") and not ordinary:
        try:
            from ..response_presentation import worker_presentation
        except ImportError:
            from response_presentation import worker_presentation
        human_answer = worker_presentation(task,task.get("worker_report"))["conversational_response"]
    parts = voice.answer_parts(ordinary["speech"] if ordinary else human_answer, keep_inline=True)
    if not parts:
        return Response(status_code=204)
    target = _piper_device(user["id"], device_id) if provider == "piper" else None
    if (provider == "yandex" and not voice.speech_configured()) or (provider == "elevenlabs" and not voice.elevenlabs_configured()):
        raise HTTPException(503, "Озвучка не настроена на сервере")
    user_id = user["id"]
    if user_id in _active_users or not check_rate_limit(f"voice:{user_id}"):
        raise HTTPException(429, "Озвучка занята. Попробуйте позже")
    _active_users.add(user_id)
    try:
        if not ordinary:
            parts = await voice.spoken_parts(goal_speech_task or task)
        if part >= len(parts):
            raise HTTPException(404, "Часть ответа не найдена")
        media_type = "audio/ogg"
        if provider == "piper":
            try:
                from ..task_runtime import send_command_to_agent
            except ImportError:
                from task_runtime import send_command_to_agent
            result = await send_command_to_agent(target, "voice.synthesize", {"text": parts[part]}, user_id=user_id)
            if not isinstance(result, dict) or result.get("error") or result.get("media_type") != "audio/wav":
                raise RuntimeError("Piper failed")
            encoded = result.get("audio_base64")
            if not isinstance(encoded, str) or len(encoded) > 5592408:
                raise RuntimeError("Invalid Piper audio size")
            audio = base64.b64decode(encoded, validate=True)
            if not 44 <= len(audio) <= 4 * 1024 * 1024:
                raise RuntimeError("Invalid Piper audio size")
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if not wav.getnframes() or wav.getcomptype() != "NONE":
                    raise RuntimeError("Invalid Piper WAV")
            media_type = "audio/wav"
        elif provider == "elevenlabs":
            audio = await voice.synthesize_elevenlabs(parts[part])
            media_type = "audio/mpeg"
        else:
            audio = await voice.synthesize(parts[part])
    except HTTPException:
        raise
    except Exception:
        # Provider responses can contain request text or credentials. Do not expose them.
        raise HTTPException(502, "Озвучка временно недоступна") from None
    finally:
        _active_users.discard(user_id)
    return Response(audio, media_type=media_type, headers={
        "Cache-Control": "no-store", "X-Voice-Parts": str(len(parts)),
    })

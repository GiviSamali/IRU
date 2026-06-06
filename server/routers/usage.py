from fastapi import APIRouter, HTTPException, Request

try:
    from ..api_support import get_current_user
    from ..database import (
        get_chat,
        get_llm_usage_summary,
        get_llm_usage_summary_for_chat,
        get_llm_usage_summary_for_poll_task,
        get_recent_llm_usage_events,
        get_recent_llm_usage_events_for_chat,
    )
except ImportError:
    from api_support import get_current_user  # type: ignore
    from database import (  # type: ignore
        get_chat,
        get_llm_usage_summary,
        get_llm_usage_summary_for_chat,
        get_llm_usage_summary_for_poll_task,
        get_recent_llm_usage_events,
        get_recent_llm_usage_events_for_chat,
    )


router = APIRouter()


def _limits_payload() -> dict:
    return {
        "daily_token_limit": None,
        "monthly_token_limit": None,
        "enforced": False,
    }


def _summary_payload(user_id: int) -> dict:
    return {
        "today": get_llm_usage_summary(user_id, "today"),
        "month": get_llm_usage_summary(user_id, "month"),
        "all_time": get_llm_usage_summary(user_id, "all_time"),
    }


@router.get("/api/usage/summary")
async def api_usage_summary(request: Request):
    user = get_current_user(request)
    user_id = int(user["id"])
    return {
        "status": "ok",
        "summary": _summary_payload(user_id),
        "limits": _limits_payload(),
        "recent_events": get_recent_llm_usage_events(user_id, limit=50),
    }


@router.get("/api/chats/{chat_id}/usage")
async def api_chat_usage(chat_id: int, request: Request):
    user = get_current_user(request)
    user_id = int(user["id"])
    if not get_chat(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {
        "status": "ok",
        "summary": get_llm_usage_summary_for_chat(user_id, chat_id),
        "limits": _limits_payload(),
        "recent_events": get_recent_llm_usage_events_for_chat(user_id, chat_id, limit=20),
    }


@router.get("/api/tasks/{poll_task_id}/usage")
async def api_task_usage(poll_task_id: str, request: Request):
    user = get_current_user(request)
    user_id = int(user["id"])
    summary = get_llm_usage_summary_for_poll_task(user_id, poll_task_id)
    return {
        "status": "ok",
        "summary": summary,
        "limits": _limits_payload(),
        "recent_events": [],
    }

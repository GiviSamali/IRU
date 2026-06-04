from __future__ import annotations

from typing import Any

try:
    from . import database as db  # type: ignore
except ImportError:
    import database as db  # type: ignore


MEMORY_TOOL_NAMES = {"memory_get_stats", "memory_list_facts"}


def _memory_user_id(user_id: str | int | None) -> str | None:
    if user_id is None:
        return None
    text = str(user_id).strip()
    return text or None


def _safe_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _format_user_fact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "text": row.get("fact_text") or row.get("text") or "",
        "category": row.get("category"),
        "source": "user",
        "created_at": row.get("created_at"),
    }


def run_memory_tool(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    user_id: str | int | None,
) -> dict[str, Any]:
    """Execute server-side memory read tools for the authenticated user."""
    user_key = _memory_user_id(user_id)
    if not user_key:
        return {
            "status": "error",
            "error": "authenticated user is required for memory tools",
            "source": "server_user_memory",
        }

    args = args or {}
    facts = [_format_user_fact(row) for row in db.get_user_facts(user_key)]

    if tool_name == "memory_get_stats":
        return {
            "status": "ok",
            "source": "server_user_memory",
            "facts_count": len(facts),
        }

    if tool_name == "memory_list_facts":
        limit = _safe_limit(args.get("limit"))
        return {
            "status": "ok",
            "source": "server_user_memory",
            "facts_count": len(facts),
            "facts": facts[:limit],
            "limit": limit,
        }

    return {
        "status": "error",
        "error": f"Unknown memory tool: {tool_name}",
        "source": "server_user_memory",
    }

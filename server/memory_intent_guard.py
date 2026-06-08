from __future__ import annotations

import re


MEMORY_WRITE_REQUIRES_INTENT = "memory_write_requires_explicit_user_intent"
MEMORY_WRITE_CORRECTION = (
    "Do not write memory unless the user explicitly asked to remember or forget something. "
    "Continue the task or call answer_text."
)


_MEMORY_WRITE_PATTERNS = (
    r"\bremember\b",
    r"\bsave\b.+\bmemory\b",
    r"\bforget\b",
    r"\bdelete\b.+\bmemory\b",
    r"\bremove\b.+\bmemory\b",
    r"\bзапомни\b",
    r"\bсохрани\b.+\bпамят",
    r"\bудали\b.+\bпамят",
    r"\bзабудь\b",
    r"\bудали\s+факт\b",
)


def has_explicit_memory_write_intent(user_message: str | None) -> bool:
    text = f" {user_message or ''} ".lower()
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _MEMORY_WRITE_PATTERNS)


def blocked_memory_write_result() -> dict:
    return {
        "status": "blocked",
        "error": MEMORY_WRITE_REQUIRES_INTENT,
    }

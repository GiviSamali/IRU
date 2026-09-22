"""SpeechKit adapter for the final, user-visible answer of an IRU task."""
import os
import re

import httpx

TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
# Even percent-encoded four-byte characters fit the v1 15 KB form limit.
CHUNK_SIZE = 900


def speech_configured() -> bool:
    return bool(os.getenv("YANDEX_API_KEY"))


def answer_parts(answer: str) -> list[str]:
    """Remove code/URLs/formatting, without generating a different answer."""
    text = re.sub(r"```[\s\S]*?(?:```|$)", " ", answer or "")
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}|>|[-*+] |\d+[.)] )\s*", "", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = []
    while text:
        end = min(len(text), CHUNK_SIZE)
        if len(text) > end:
            boundary = text.rfind(" ", 0, end + 1)
            if boundary > 0:
                end = boundary
        parts.append(text[:end])
        text = text[end:].lstrip()
    return parts


async def synthesize(text: str) -> bytes:
    key = os.getenv("YANDEX_API_KEY")
    if not key:
        raise RuntimeError("Speech is not configured")
    data = {"text": text, "lang": "ru-RU", "voice": "zahar",
            "emotion": "neutral", "speed": "1.2", "format": "oggopus"}
    folder = os.getenv("YANDEX_FOLDER_ID")
    if folder:
        data["folderId"] = folder
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(TTS_URL, headers={"Authorization": f"Api-Key {key}"}, data=data)
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("Empty speech response")
        return response.content

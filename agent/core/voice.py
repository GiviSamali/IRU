"""Local script TTS; paths come only from the agent owner's configuration."""
from __future__ import annotations
import base64
import subprocess
import tempfile
import wave
from pathlib import Path

MAX_AUDIO_BYTES = 4 * 1024 * 1024

def synthesize(text: str, config: dict) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > 900 or "\x00" in text:
        raise ValueError("Некорректный текст озвучки")
    python = Path(str(config.get("silero_python") or ""))
    script = Path(str(config.get("silero_script") or ""))
    if not python.is_absolute() or not python.is_file() or not script.is_absolute() or not script.is_file():
        raise ValueError("Укажите Python и скрипт Silero в настройках агента")
    with tempfile.TemporaryDirectory(prefix="iru-voice-") as directory:
        # say.py writes output.wav relative to cwd. Never overwrite the owner's file.
        try:
            result = subprocess.run([str(python), str(script), text], cwd=directory,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=50,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            # TimeoutExpired includes the complete text argument; never log it.
            raise RuntimeError("Silero не завершил озвучку за 50 секунд") from None
        output = Path(directory) / "output.wav"
        if result.returncode or not output.is_file():
            raise RuntimeError("Скрипт Silero не создал аудио")
        if not 44 <= output.stat().st_size <= MAX_AUDIO_BYTES:
            raise RuntimeError("Аудио Silero превышает допустимый размер или пусто")
        with wave.open(str(output), "rb") as audio:
            if audio.getnframes() == 0 or audio.getcomptype() != "NONE":
                raise RuntimeError("Некорректный WAV Silero")
        return {"audio_base64": base64.b64encode(output.read_bytes()).decode("ascii"),
                "media_type": "audio/wav"}

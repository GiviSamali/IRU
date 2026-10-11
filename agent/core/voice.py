"""Local Piper TTS; paths come only from the agent owner's configuration."""
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
    python = Path(str(config.get("piper_python") or ""))
    settings = Path(str(config.get("piper_settings") or ""))
    if not python.is_absolute() or not python.is_file() or not settings.is_absolute() or not settings.is_file():
        raise ValueError("Укажите Python и settings.json Piper в настройках агента")
    code = """
import json, sys, wave
from pathlib import Path
from piper import PiperVoice, SynthesisConfig
settings = Path(sys.argv[1])
cfg = json.loads(settings.read_text(encoding='utf-8-sig'))
speed = float(cfg.get('speed', 1.25))
volume = float(cfg.get('volume', 0.85))
if not 0.5 <= speed <= 2 or not 0 <= volume <= 1:
    raise ValueError('Invalid Piper speed/volume')
voice = PiperVoice.load(str((settings.parent / cfg['model']).resolve()))
config = SynthesisConfig(length_scale=1/speed, volume=volume,
    noise_scale=cfg.get('noise_scale'), noise_w_scale=cfg.get('noise_w_scale'),
    normalize_audio=bool(cfg.get('normalize_audio', True)))
with wave.open('output.wav', 'wb') as wav:
    voice.synthesize_wav(sys.argv[2], wav, syn_config=config)
"""
    with tempfile.TemporaryDirectory(prefix="iru-voice-") as directory:
        # Keep audio isolated from the owner's playground output.
        try:
            result = subprocess.run([str(python), "-c", code, str(settings), text], cwd=directory,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=50,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            # TimeoutExpired includes the complete text argument; never log it.
            raise RuntimeError("Piper не завершил озвучку за 50 секунд") from None
        output = Path(directory) / "output.wav"
        if result.returncode or not output.is_file():
            raise RuntimeError("Piper не создал аудио")
        if not 44 <= output.stat().st_size <= MAX_AUDIO_BYTES:
            raise RuntimeError("Аудио Piper превышает допустимый размер или пусто")
        with wave.open(str(output), "rb") as audio:
            if audio.getnframes() == 0 or audio.getcomptype() != "NONE":
                raise RuntimeError("Некорректный WAV Piper")
        return {"audio_base64": base64.b64encode(output.read_bytes()).decode("ascii"),
                "media_type": "audio/wav"}

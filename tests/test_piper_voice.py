"""Focused checks for the local TTS boundary. Prepared, not automatically run."""
import base64
import importlib.util
import wave
from pathlib import Path
from types import SimpleNamespace
import pytest

SPEC = importlib.util.spec_from_file_location("iru_local_voice", Path(__file__).parents[1] / "agent/core/voice.py")
local_voice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_voice)


def test_script_text_is_one_argument_and_audio_is_temporary(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    script = tmp_path / "settings.json"
    python.touch(); script.touch()
    text = 'Ответ; $(other) "quoted"'
    observed = {}
    def run(args, **options):
        assert args[0] == str(python) and args[1] == "-c"
        assert args[-2:] == [str(script), text]
        assert not options.get("shell")
        observed["cwd"] = Path(options["cwd"])
        with wave.open(str(observed["cwd"] / "output.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 100)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local_voice.subprocess, "run", run)
    result = local_voice.synthesize(text, {"piper_python": str(python), "piper_settings": str(script)})
    assert result["media_type"] == "audio/wav"
    assert base64.b64decode(result["audio_base64"]).startswith(b"RIFF")
    assert not observed["cwd"].exists()


def test_piper_cannot_choose_other_users_device(monkeypatch):
    from server.routers import voice
    from server import runtime_state
    from fastapi import HTTPException
    monkeypatch.setattr(runtime_state, "devices", {"2:pc": {"user_id": 2}})
    with pytest.raises(HTTPException) as error:
        voice._piper_device(1, "pc")
    assert error.value.status_code == 503
    assert voice._piper_device(2, "pc") == "2:pc"


def test_stress_marks_preserve_negative_speech_guard():
    from server.voice import conversational_speech
    speech = "Не удалось вы́полнить."
    assert conversational_speech(speech, "Не удалось выполнить.") == speech
    assert conversational_speech("Вы́полнила.", "Не удалось выполнить.") == ""
    assert conversational_speech("Вы́полнила.", "Проверяю.") == ""

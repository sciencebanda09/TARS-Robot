from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pyttsx3
except Exception as exc:  # pragma: no cover
    pyttsx3 = None
    logger.warning("pyttsx3 unavailable: %s", exc)

try:
    import speech_recognition as sr
except Exception as exc:  # pragma: no cover
    sr = None
    logger.warning("speech_recognition unavailable: %s", exc)

_tts_engine = None
_recognizer = None
_microphone = None


def init_voice(rate: int = 185) -> None:
    """Initialize TTS and speech recognition once."""
    global _tts_engine, _recognizer, _microphone

    if pyttsx3 and _tts_engine is None:
        try:
            _tts_engine = pyttsx3.init()
            _tts_engine.setProperty("rate", rate)
        except Exception as exc:
            logger.error("Speech synthesizer failed: %s", exc)
            _tts_engine = None

    if sr and _recognizer is None:
        _recognizer = sr.Recognizer()
        try:
            _microphone = sr.Microphone()
        except Exception as exc:
            logger.warning("Microphone unavailable: %s", exc)
            _microphone = None


def set_speech_rate(rate: int) -> None:
    if _tts_engine is not None:
        try:
            _tts_engine.setProperty("rate", int(rate))
        except Exception as exc:
            logger.warning("Unable to update speech rate: %s", exc)


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text


def speak(text: str, rate: Optional[int] = None) -> None:
    text = _clean_text(text)
    print(f"TARS: {text}")

    if rate is not None:
        set_speech_rate(rate)

    if _tts_engine is None:
        return

    try:
        _tts_engine.say(text)
        _tts_engine.runAndWait()
    except Exception as exc:
        logger.error("Audio output failed: %s", exc)


def listen_command(timeout: float = 4.0, phrase_time_limit: float = 6.0, ambient_adjust: float = 0.6) -> Optional[str]:
    """Listen for a command and return normalized lower-case text."""
    if sr is None:
        return None

    if _recognizer is None or _microphone is None:
        init_voice()

    if _recognizer is None or _microphone is None:
        return None

    try:
        with _microphone as source:
            if ambient_adjust and ambient_adjust > 0:
                _recognizer.adjust_for_ambient_noise(source, duration=ambient_adjust)
            audio = _recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        text = _recognizer.recognize_google(audio)
        return _clean_text(text).lower()
    except (sr.WaitTimeoutError, sr.UnknownValueError):
        return None
    except sr.RequestError:
        speak("Network drop. Cloud translation unavailable.")
        return None
    except Exception as exc:
        logger.warning("Voice input error: %s", exc)
        return None


init_voice()

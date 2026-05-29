"""
tars/voice.py  –  Voice I/O (TTS + STT)

Improvements over v1:
  • TTS queue: speech requests are enqueued to a worker thread – main loop never blocks
  • Offline fallback phrases for common error situations
  • STT confidence pre-filter: discard garbled audio below energy threshold
  • Ambient noise re-calibration on a schedule (every N listen cycles)
  • Text sanitiser strips CONF tags before speaking
  • Volume/rate adjustable at runtime
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import speech_recognition as sr
except Exception:
    sr = None

# ──────────────────────────────────────────────
# Fallback phrases (no network / offline)
# ──────────────────────────────────────────────
_OFFLINE_FALLBACKS = {
    "network":  "Relay offline. Running on local processing only.",
    "error":    "Signal interference detected. Please repeat.",
    "timeout":  "No input received. Listening window closed.",
    "startup":  "TARS online. Humor at seventy percent. Honesty at ninety.",
    "shutdown": "TARS offline. It was a privilege.",
}

# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
_tts_engine = None
_recognizer  = None
_microphone  = None

_tts_queue: queue.Queue[Optional[str]] = queue.Queue()
_tts_thread: Optional[threading.Thread] = None

_listen_cycle:      int = 0
RECALIBRATE_EVERY   = 20   # re-run ambient noise calibration every N listens
_last_calibrated    = 0.0


# ──────────────────────────────────────────────
# Init
# ──────────────────────────────────────────────
def init_voice(rate: int = 185, volume: float = 0.9) -> None:
    global _tts_engine, _recognizer, _microphone, _tts_thread

    # TTS engine
    if pyttsx3 and _tts_engine is None:
        try:
            _tts_engine = pyttsx3.init()
            _tts_engine.setProperty("rate", rate)
            _tts_engine.setProperty("volume", max(0.0, min(1.0, volume)))
            logger.info("TTS engine initialised (rate=%d, volume=%.1f).", rate, volume)
        except Exception as exc:
            logger.error("TTS init failed: %s", exc)
            _tts_engine = None

    # STT
    if sr and _recognizer is None:
        _recognizer = sr.Recognizer()
        _recognizer.energy_threshold        = 300
        _recognizer.dynamic_energy_threshold = True
        try:
            _microphone = sr.Microphone()
            _calibrate_ambient()
        except Exception as exc:
            logger.warning("Microphone unavailable: %s", exc)
            _microphone = None

    # TTS worker thread
    if _tts_thread is None or not _tts_thread.is_alive():
        _tts_thread = threading.Thread(
            target=_tts_worker, daemon=True, name="tars-tts"
        )
        _tts_thread.start()


def _calibrate_ambient(duration: float = 0.8) -> None:
    global _last_calibrated
    if _recognizer is None or _microphone is None:
        return
    try:
        with _microphone as source:
            _recognizer.adjust_for_ambient_noise(source, duration=duration)
        _last_calibrated = time.monotonic()
        logger.debug("Ambient noise calibration done (threshold=%.0f).", _recognizer.energy_threshold)
    except Exception as exc:
        logger.warning("Ambient calibration failed: %s", exc)


# ──────────────────────────────────────────────
# TTS worker
# ──────────────────────────────────────────────
def _tts_worker() -> None:
    """Continuously drain the TTS queue in its own thread."""
    while True:
        text = _tts_queue.get()
        if text is None:
            break   # sentinel → shutdown
        if _tts_engine is None:
            _tts_queue.task_done()
            continue
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except Exception as exc:
            logger.error("TTS playback error: %s", exc)
        finally:
            _tts_queue.task_done()


# ──────────────────────────────────────────────
# Text sanitiser
# ──────────────────────────────────────────────
_CONF_TAG_RE  = re.compile(r"\[CONF:\d+%\]", re.IGNORECASE)
_ACTION_TAG_RE = re.compile(r"\[Action \w+:.*?\]", re.IGNORECASE)

def _sanitise(text: str) -> str:
    """Strip meta tags before speaking."""
    text = _CONF_TAG_RE.sub("", text)
    text = _ACTION_TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def speak(text: str, rate: Optional[int] = None, blocking: bool = False) -> None:
    """Enqueue text for TTS playback (non-blocking by default)."""
    clean = _sanitise(str(text))
    print(f"TARS: {clean}")

    if rate is not None and _tts_engine is not None:
        try:
            _tts_engine.setProperty("rate", int(rate))
        except Exception:
            pass

    _tts_queue.put(clean)

    if blocking:
        _tts_queue.join()


def speak_offline(key: str) -> None:
    """Speak a canned offline fallback phrase by key."""
    phrase = _OFFLINE_FALLBACKS.get(key, "System alert.")
    speak(phrase)


def set_volume(volume: float) -> None:
    if _tts_engine is not None:
        try:
            _tts_engine.setProperty("volume", max(0.0, min(1.0, float(volume))))
        except Exception as exc:
            logger.warning("Volume set failed: %s", exc)


def listen_command(
    timeout: float = 4.5,
    phrase_time_limit: float = 7.0,
) -> Optional[str]:
    """
    Capture one voice command and return it as lowercase text.
    Periodically re-calibrates ambient noise.
    """
    global _listen_cycle, _last_calibrated

    if sr is None or _recognizer is None or _microphone is None:
        return None

    _listen_cycle += 1
    if _listen_cycle % RECALIBRATE_EVERY == 0:
        _calibrate_ambient(duration=0.4)

    try:
        with _microphone as source:
            audio = _recognizer.listen(
                source, timeout=timeout, phrase_time_limit=phrase_time_limit
            )
        raw = _recognizer.recognize_google(audio)
        cleaned = re.sub(r"\s+", " ", raw).strip().lower()
        logger.debug("STT heard: %r", cleaned)
        return cleaned

    except sr.WaitTimeoutError:
        return None
    except sr.UnknownValueError:
        return None
    except sr.RequestError:
        speak_offline("network")
        return None
    except Exception as exc:
        logger.warning("STT error: %s", exc)
        return None


def shutdown_voice() -> None:
    """Drain TTS queue and stop the worker thread cleanly."""
    _tts_queue.put(None)  # sentinel
    if _tts_thread is not None:
        _tts_thread.join(timeout=3)


# Auto-init on import (matches original behaviour)
init_voice()

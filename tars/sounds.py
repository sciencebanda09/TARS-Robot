"""
tars/sounds.py  –  Sound Effects Engine  (NEW MODULE)

Generates simple tones/beeps using numpy + simpleaudio when available.
Falls back to console print if audio libraries are missing.

Effects supported: beep, power_up, power_down, error, success, thinking
"""

from __future__ import annotations

import logging
import math
import threading
from typing import List, Tuple

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import simpleaudio as sa
    _AUDIO_OK = True
except Exception as exc:
    np = None       # type: ignore[assignment]
    sa = None       # type: ignore[assignment]
    _AUDIO_OK = False
    logger.info("Audio libs unavailable – sound effects mocked: %s", exc)

SAMPLE_RATE = 44_100   # Hz
VOLUME      = 0.4      # 0.0 – 1.0


# ──────────────────────────────────────────────
# Tone synthesis helpers
# ──────────────────────────────────────────────
def _sine_wave(freq: float, duration_s: float, fade_ms: int = 20) -> "np.ndarray":
    t = np.linspace(0, duration_s, int(SAMPLE_RATE * duration_s), endpoint=False)
    wave = VOLUME * np.sin(2 * np.pi * freq * t)

    # apply tiny fade-in/out to avoid clicks
    fade_samples = int(SAMPLE_RATE * fade_ms / 1000)
    if fade_samples > 0 and len(wave) > fade_samples * 2:
        ramp = np.linspace(0, 1, fade_samples)
        wave[:fade_samples]  *= ramp
        wave[-fade_samples:] *= ramp[::-1]

    return wave


def _concat(tones: List[Tuple[float, float]]) -> "np.ndarray":
    """tones = [(freq_hz, duration_s), ...]"""
    return np.concatenate([_sine_wave(f, d) for f, d in tones])


# ──────────────────────────────────────────────
# Effect library
# ──────────────────────────────────────────────
def _build_effect(name: str) -> "np.ndarray":
    if name == "beep":
        return _sine_wave(880, 0.12)
    if name == "power_up":
        return _concat([(330, 0.08), (440, 0.08), (550, 0.08), (660, 0.15)])
    if name == "power_down":
        return _concat([(660, 0.08), (550, 0.08), (440, 0.08), (330, 0.18)])
    if name == "error":
        return _concat([(200, 0.15), (180, 0.15), (160, 0.20)])
    if name == "success":
        return _concat([(523, 0.07), (659, 0.07), (784, 0.14)])
    if name == "thinking":
        # Pulsing mid-tone
        return _concat([(440, 0.06), (0, 0.04), (440, 0.06), (0, 0.04), (440, 0.06)])
    return _sine_wave(440, 0.1)


def _play_array(wave: "np.ndarray") -> None:
    audio = (wave * 32_767).astype("int16")
    play_obj = sa.play_buffer(audio, 1, 2, SAMPLE_RATE)
    play_obj.wait_done()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def play_effect(effect: str) -> None:
    """Play a named sound effect non-blocking."""
    if not _AUDIO_OK or np is None or sa is None:
        logger.debug("Sound effect (mocked): %s", effect)
        return

    def _worker() -> None:
        try:
            wave = _build_effect(effect)
            _play_array(wave)
        except Exception as exc:
            logger.warning("Sound effect '%s' failed: %s", effect, exc)

    threading.Thread(target=_worker, daemon=True, name=f"tars-sfx-{effect}").start()

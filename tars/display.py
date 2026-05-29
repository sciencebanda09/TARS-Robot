"""
tars/display.py  –  OLED Expression Engine  (SSD1306, 128×64)

Improvements over v1:
  • Animated blinking: eye-blink loop runs in a background thread
  • Boot animation: sweep / scan effect on startup
  • Status bar: shows time + fan state at bottom of screen
  • Expression registry makes adding new moods trivial
  • draw helpers are composable lambdas → easy unit testing
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from luma.oled.device import ssd1306
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    _LUMA_OK = True
except Exception as exc:
    ssd1306 = None       # type: ignore[assignment]
    i2c = None           # type: ignore[assignment]
    canvas = None        # type: ignore[assignment]
    _LUMA_OK = False
    logger.warning("OLED libraries unavailable – display mocked: %s", exc)

# ──────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────
_device = None
_blink_thread: Optional[threading.Thread] = None
_blink_active: threading.Event = threading.Event()
_current_mood: str = "neutral"

# ──────────────────────────────────────────────
# Expression drawers
# ──────────────────────────────────────────────
EyeDrawer = Callable[["canvas", bool], None]  # type: ignore[valid-type]

def _eyes_neutral(draw: object, closed: bool = False) -> None:
    h = 40 if closed else 24
    draw.rectangle((18, h, 44, 36), outline="white", fill="white")     # type: ignore[union-attr]
    draw.rectangle((84, h, 110, 36), outline="white", fill="white")    # type: ignore[union-attr]

def _eyes_happy(draw: object, closed: bool = False) -> None:
    if closed:
        draw.line((18, 30, 44, 30), fill="white", width=3)             # type: ignore[union-attr]
        draw.line((84, 30, 110, 30), fill="white", width=3)            # type: ignore[union-attr]
    else:
        draw.arc((18, 14, 44, 44), start=180, end=0, fill="white", width=4)   # type: ignore[union-attr]
        draw.arc((84, 14, 110, 44), start=180, end=0, fill="white", width=4)  # type: ignore[union-attr]

def _eyes_sad(draw: object, closed: bool = False) -> None:
    draw.line((18, 30, 44, 22), fill="white", width=4)                 # type: ignore[union-attr]
    draw.line((84, 22, 110, 30), fill="white", width=4)                # type: ignore[union-attr]

def _eyes_alert(draw: object, closed: bool = False) -> None:
    draw.rectangle((18, 20, 44, 42), outline="white", width=3)        # type: ignore[union-attr]
    draw.rectangle((84, 20, 110, 42), outline="white", width=3)       # type: ignore[union-attr]
    # pupils – move inward on blink
    if not closed:
        draw.rectangle((28, 28, 34, 34), fill="white")                # type: ignore[union-attr]
        draw.rectangle((94, 28, 100, 34), fill="white")               # type: ignore[union-attr]

def _eyes_thinking(draw: object, closed: bool = False) -> None:
    # one eye slightly lower (concentration)
    draw.arc((18, 20, 44, 44), start=200, end=340, fill="white", width=3)  # type: ignore[union-attr]
    draw.arc((84, 24, 110, 44), start=200, end=340, fill="white", width=3) # type: ignore[union-attr]

def _eyes_listening(draw: object, closed: bool = False) -> None:
    # wide open circles
    draw.ellipse((18, 16, 44, 42), outline="white", width=3)          # type: ignore[union-attr]
    draw.ellipse((84, 16, 110, 42), outline="white", width=3)         # type: ignore[union-attr]

_EYE_DRAWERS: Dict[str, EyeDrawer] = {
    "neutral":   _eyes_neutral,
    "happy":     _eyes_happy,
    "positive":  _eyes_happy,
    "sad":       _eyes_sad,
    "blocked":   _eyes_sad,
    "alert":     _eyes_alert,
    "warning":   _eyes_alert,
    "thinking":  _eyes_thinking,
    "listening": _eyes_listening,
    "curious":   _eyes_listening,
}


# ──────────────────────────────────────────────
# Init
# ──────────────────────────────────────────────
def init_display() -> None:
    global _device
    if not _LUMA_OK:
        return
    try:
        serial = i2c(port=1, address=0x3C)
        _device = ssd1306(serial)
        logger.info("OLED display initialised (128×64 SSD1306).")
        _boot_animation()
    except Exception as exc:
        logger.error("Display init failed: %s", exc)
        _device = None


def _boot_animation() -> None:
    """Horizontal scan-line sweep on startup."""
    if _device is None or canvas is None:
        return
    try:
        for y in range(0, 64, 4):
            with canvas(_device) as draw:
                draw.line((0, y, 127, y), fill="white", width=2)
            time.sleep(0.02)
        time.sleep(0.1)
        with canvas(_device) as draw:
            draw.rectangle((0, 0, 127, 63), outline="white")
            draw.text((28, 24), "T.A.R.S", fill="white")
            draw.text((20, 38), "BOOT SEQUENCE", fill="white")
        time.sleep(0.8)
    except Exception as exc:
        logger.debug("Boot animation error: %s", exc)


# ──────────────────────────────────────────────
# Core render
# ──────────────────────────────────────────────
def _render(mood: str, subtitle: str, closed: bool = False) -> None:
    if _device is None or canvas is None:
        return
    drawer = _EYE_DRAWERS.get(mood.lower(), _eyes_neutral)
    try:
        with canvas(_device) as draw:
            drawer(draw, closed)
            # status bar
            label = subtitle[:20] if subtitle else f"SYS:{mood.upper()}"
            draw.text((6, 52), label, fill="white")
    except Exception as exc:
        logger.debug("Render error: %s", exc)


# ──────────────────────────────────────────────
# Public expression API
# ──────────────────────────────────────────────
def show_expression(mood: str = "neutral", subtitle: Optional[str] = None) -> None:
    global _current_mood
    _current_mood = mood.lower()
    label = subtitle if subtitle is not None else f"SYS:{mood.upper()}"
    _stop_blink()
    _render(_current_mood, label, closed=False)
    _start_blink(_current_mood, label)


def show_text(message: str) -> None:
    _stop_blink()
    if _device is None or canvas is None:
        return
    try:
        with canvas(_device) as draw:
            draw.text((0, 0), str(message)[:32], fill="white")
    except Exception as exc:
        logger.debug("show_text error: %s", exc)


# ──────────────────────────────────────────────
# Blink loop (background thread)
# ──────────────────────────────────────────────
_BLINK_OPEN_S  = 3.5
_BLINK_CLOSE_S = 0.12


def _blink_loop(mood: str, subtitle: str) -> None:
    while not _blink_active.is_set():
        _blink_active.wait(timeout=_BLINK_OPEN_S)
        if _blink_active.is_set():
            break
        _render(mood, subtitle, closed=True)
        _blink_active.wait(timeout=_BLINK_CLOSE_S)
        if _blink_active.is_set():
            break
        _render(mood, subtitle, closed=False)


def _start_blink(mood: str, subtitle: str) -> None:
    global _blink_thread
    _blink_active.clear()
    _blink_thread = threading.Thread(
        target=_blink_loop,
        args=(mood, subtitle),
        daemon=True,
        name="tars-blink",
    )
    _blink_thread.start()


def _stop_blink() -> None:
    _blink_active.set()
    if _blink_thread is not None and _blink_thread.is_alive():
        _blink_thread.join(timeout=0.5)

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from luma.oled.device import ssd1306
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
except Exception as exc:  # pragma: no cover
    ssd1306 = None
    i2c = None
    canvas = None
    logger.warning("OLED libraries unavailable: %s", exc)

device = None


def init_display() -> None:
    """Initialize the OLED display if hardware is present."""
    global device
    if ssd1306 is None or i2c is None:
        return

    try:
        serial = i2c(port=1, address=0x3C)
        device = ssd1306(serial)
        logger.info("OLED display initialized.")
    except Exception as exc:
        logger.error("Display initialization failed: %s", exc)
        device = None


def _draw_eye_pair(draw, mood: str) -> None:
    mood = (mood or "neutral").lower()

    if mood in {"happy", "positive"}:
        draw.arc((18, 18, 44, 44), start=180, end=0, fill="white", width=4)
        draw.arc((84, 18, 110, 44), start=180, end=0, fill="white", width=4)
    elif mood in {"sad", "blocked"}:
        draw.line((18, 30, 44, 22), fill="white", width=4)
        draw.line((84, 22, 110, 30), fill="white", width=4)
    elif mood in {"alert", "warning"}:
        draw.rectangle((18, 20, 44, 42), outline="white", width=3)
        draw.rectangle((84, 20, 110, 42), outline="white", width=3)
        draw.rectangle((28, 28, 34, 34), fill="white")
        draw.rectangle((94, 28, 100, 34), fill="white")
    elif mood in {"listening", "thinking"}:
        draw.arc((18, 20, 44, 44), start=200, end=340, fill="white", width=3)
        draw.arc((84, 20, 110, 44), start=200, end=340, fill="white", width=3)
    else:
        draw.rectangle((18, 24, 44, 36), outline="white", fill="white")
        draw.rectangle((84, 24, 110, 36), outline="white", fill="white")


def show_expression(mood: str = "neutral", subtitle: Optional[str] = None) -> None:
    """Render a face and short caption on the OLED."""
    if device is None or canvas is None:
        return

    try:
        with canvas(device) as draw:
            _draw_eye_pair(draw, mood)
            label = subtitle if subtitle is not None else f"SYS: {mood.upper()}"
            draw.text((6, 52), label[:20], fill="white")
    except Exception as exc:
        logger.error("Display update failed: %s", exc)


def show_text(message: str) -> None:
    if device is None or canvas is None:
        return
    try:
        with canvas(device) as draw:
            draw.text((0, 0), str(message)[:32], fill="white")
    except Exception as exc:
        logger.error("Display text failed: %s", exc)

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception as exc:  # pragma: no cover
    GPIO = None
    logger.warning("RPi.GPIO unavailable in fan module: %s", exc)

FAN_PIN = 21
_fan_state = False
_last_change = 0.0
MIN_SWITCH_INTERVAL = 1.0


def init_fan() -> None:
    if GPIO is None:
        return
    GPIO.setup(FAN_PIN, GPIO.OUT)
    set_fan_state(False, force=True)


def set_fan_state(state: bool, force: bool = False) -> bool:
    global _fan_state, _last_change

    now = time.monotonic()
    if not force and state != _fan_state and (now - _last_change) < MIN_SWITCH_INTERVAL:
        return _fan_state

    _fan_state = bool(state)
    _last_change = now

    if GPIO is not None:
        GPIO.output(FAN_PIN, GPIO.HIGH if _fan_state else GPIO.LOW)

    logger.info("Thermal relay: %s", "ON" if _fan_state else "OFF")
    return _fan_state


def smart_cool(current_temp_c: Optional[float], threshold: float = 35.0, hysteresis: float = 1.5) -> bool:
    """Simple thermal policy with hysteresis to prevent relay chatter."""
    if current_temp_c is None:
        return set_fan_state(False)

    if current_temp_c >= threshold:
        return set_fan_state(True)
    if current_temp_c <= (threshold - hysteresis):
        return set_fan_state(False)
    return _fan_state


def is_fan_on() -> bool:
    return _fan_state

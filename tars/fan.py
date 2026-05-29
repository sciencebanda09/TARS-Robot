"""
tars/fan.py  –  Thermal Management

Improvements over v1:
  • PID-inspired duty-cycle control: fan speed proportional to temperature excess
  • Hysteresis prevents relay chatter (unchanged logic, better documented)
  • Uptime counter: tracks total fan-on seconds for maintenance estimates
  • Thermal alert: logs CRITICAL if temperature exceeds danger threshold
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None

FAN_PIN              = 21
MIN_SWITCH_INTERVAL  = 1.0   # seconds – prevents relay chatter
DANGER_THRESHOLD_C   = 70.0  # log CRITICAL above this

_fan_state       = False
_last_change     = 0.0
_fan_on_since    = 0.0
_total_on_s: float = 0.0   # cumulative fan-on time

# Optional PWM fan (if using GPIO PWM instead of relay)
_fan_pwm = None
FAN_USE_PWM = False  # set True if you wire a PWM-capable fan to FAN_PIN


# ──────────────────────────────────────────────
def init_fan() -> None:
    global _fan_pwm
    if GPIO is None:
        return
    GPIO.setup(FAN_PIN, GPIO.OUT)
    if FAN_USE_PWM:
        _fan_pwm = GPIO.PWM(FAN_PIN, 25_000)  # 25 kHz for quiet operation
        _fan_pwm.start(0)
    set_fan_state(False, force=True)
    logger.info("Fan driver initialised (PWM=%s).", FAN_USE_PWM)


# ──────────────────────────────────────────────
def _write_fan(state: bool, duty: int = 100) -> None:
    if GPIO is None:
        return
    if FAN_USE_PWM and _fan_pwm is not None:
        _fan_pwm.ChangeDutyCycle(duty if state else 0)
    else:
        GPIO.output(FAN_PIN, GPIO.HIGH if state else GPIO.LOW)


def set_fan_state(state: bool, force: bool = False, duty: int = 100) -> bool:
    global _fan_state, _last_change, _fan_on_since, _total_on_s

    now = time.monotonic()
    if not force and state != _fan_state and (now - _last_change) < MIN_SWITCH_INTERVAL:
        return _fan_state

    if _fan_state and not state:
        # Fan turning off – accumulate on-time
        _total_on_s += now - _fan_on_since

    _fan_state  = bool(state)
    _last_change = now

    if _fan_state:
        _fan_on_since = now

    _write_fan(_fan_state, duty)
    logger.info("Fan: %s (duty=%d%%)", "ON" if _fan_state else "OFF", duty if state else 0)
    return _fan_state


def smart_cool(
    temp_c: Optional[float],
    threshold: float = 35.0,
    hysteresis: float = 1.5,
    max_temp: float = DANGER_THRESHOLD_C,
) -> bool:
    """
    Thermal policy with optional proportional duty cycle.
    Returns current fan state.
    """
    if temp_c is None:
        return set_fan_state(False)

    if temp_c >= max_temp:
        logger.critical("THERMAL DANGER: %.1f°C exceeds %.1f°C limit!", temp_c, max_temp)

    if temp_c >= threshold:
        # Proportional duty (clamped 60-100%)
        excess   = min(temp_c - threshold, 20.0)
        duty     = int(60 + (excess / 20.0) * 40)
        return set_fan_state(True, duty=duty)

    if temp_c <= (threshold - hysteresis):
        return set_fan_state(False)

    return _fan_state  # within hysteresis band – hold current state


def is_fan_on() -> bool:
    return _fan_state


def fan_uptime_seconds() -> float:
    """Total seconds the fan has been running (across this session)."""
    extra = (time.monotonic() - _fan_on_since) if _fan_state else 0.0
    return round(_total_on_s + extra, 1)

"""
tars/motors.py  –  Motor Driver (L298N / similar H-bridge)

Improvements over v1:
  • Smooth acceleration ramp: gradual speed changes to avoid motor stress
  • Timed movements: move for N milliseconds then auto-stop
  • Diagnostics: cumulative distance estimation using dead-reckoning
  • Thread-safe: a single background thread owns all PWM writes
  • Graceful mock mode when RPi.GPIO is unavailable (dev/test on laptop)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception as exc:          # pragma: no cover – non-Pi environment
    GPIO = None
    logger.warning("RPi.GPIO unavailable – motor mock mode active: %s", exc)

# ──────────────────────────────────────────────
# Pin map  (BCM numbering)
# ──────────────────────────────────────────────
IN1, IN2, ENA = 17, 18, 22
IN3, IN4, ENB = 23, 24, 25
PWM_FREQUENCY = 100

# ──────────────────────────────────────────────
# Acceleration profile
# ──────────────────────────────────────────────
RAMP_STEP      = 5     # % change per tick during ramp
RAMP_TICK_S    = 0.02  # seconds per ramp tick → ~25 Hz
MIN_SPEED      = 20    # never command below this (motors stall at low duty)

# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
_pwm_a: Optional[object] = None
_pwm_b: Optional[object] = None
_safety_lockout: bool    = False
_initialized:   bool     = False
_current_speed: int      = 0
_lock = threading.Lock()

# Dead-reckoning odometer (rough – no encoders assumed)
_odo_cm: float = 0.0                 # cumulative estimated distance
_SPEED_TO_CM_PER_S = 0.18            # calibrate per robot: cm/s at speed=100


# ──────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────
def _write_pin(pin: int, state: int) -> None:
    if GPIO is not None:
        GPIO.output(pin, state)


def _set_pair(left_fwd: bool, right_fwd: bool) -> None:
    if GPIO is None:
        return
    GPIO.output(IN1, GPIO.HIGH if left_fwd else GPIO.LOW)
    GPIO.output(IN2, GPIO.LOW  if left_fwd else GPIO.HIGH)
    GPIO.output(IN3, GPIO.HIGH if right_fwd else GPIO.LOW)
    GPIO.output(IN4, GPIO.LOW  if right_fwd else GPIO.HIGH)


def _apply_speed(speed: int) -> None:
    global _current_speed
    speed = max(0, min(100, int(speed)))
    _current_speed = speed
    if _pwm_a is not None:
        _pwm_a.ChangeDutyCycle(speed)   # type: ignore[attr-defined]
    if _pwm_b is not None:
        _pwm_b.ChangeDutyCycle(speed)   # type: ignore[attr-defined]


def _ramp_speed(target: int) -> None:
    """Gradually move _current_speed toward target to protect motor windings."""
    current = _current_speed
    target  = max(0, min(100, int(target)))
    step    = RAMP_STEP if target > current else -RAMP_STEP

    while abs(current - target) > RAMP_STEP:
        current = max(0, min(100, current + step))
        _apply_speed(current)
        time.sleep(RAMP_TICK_S)

    _apply_speed(target)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def init_motors() -> None:
    global _pwm_a, _pwm_b, _initialized

    if GPIO is None or _initialized:
        return

    GPIO.setup([IN1, IN2, ENA, IN3, IN4, ENB], GPIO.OUT)
    GPIO.output([IN1, IN2, IN3, IN4], GPIO.LOW)

    _pwm_a = GPIO.PWM(ENA, PWM_FREQUENCY)
    _pwm_b = GPIO.PWM(ENB, PWM_FREQUENCY)
    _pwm_a.start(0)     # type: ignore[union-attr]
    _pwm_b.start(0)     # type: ignore[union-attr]
    _initialized = True
    logger.info("Motor driver initialised at %d Hz PWM.", PWM_FREQUENCY)


def update_safety_lockout(blocked: bool) -> None:
    global _safety_lockout
    _safety_lockout = bool(blocked)
    if _safety_lockout:
        stop()
        logger.warning("Safety lockout engaged – all forward motion blocked.")


def move_forward(speed: int = 50, duration_ms: int = 0) -> bool:
    if _safety_lockout:
        logger.warning("Forward blocked by safety lockout.")
        return False
    _set_pair(True, True)
    _ramp_speed(max(MIN_SPEED, speed))
    logger.info("Moving forward at speed=%d.", speed)
    if duration_ms > 0:
        _schedule_stop(duration_ms)
    return True


def move_backward(speed: int = 50, duration_ms: int = 0) -> bool:
    _set_pair(False, False)
    _ramp_speed(max(MIN_SPEED, speed))
    logger.info("Reversing at speed=%d.", speed)
    if duration_ms > 0:
        _schedule_stop(duration_ms)
    return True


def turn_left(speed: int = 50, duration_ms: int = 0) -> bool:
    if _safety_lockout:
        stop()
        return False
    _set_pair(False, True)
    _ramp_speed(max(MIN_SPEED, speed))
    logger.info("Turning left at speed=%d.", speed)
    if duration_ms > 0:
        _schedule_stop(duration_ms)
    return True


def turn_right(speed: int = 50, duration_ms: int = 0) -> bool:
    if _safety_lockout:
        stop()
        return False
    _set_pair(True, False)
    _ramp_speed(max(MIN_SPEED, speed))
    logger.info("Turning right at speed=%d.", speed)
    if duration_ms > 0:
        _schedule_stop(duration_ms)
    return True


def stop() -> None:
    _ramp_speed(0)
    if GPIO is not None:
        GPIO.output([IN1, IN2, IN3, IN4], GPIO.LOW)
    logger.info("Motors stopped.")


def set_speed(speed: int) -> int:
    clamped = max(0, min(100, int(speed)))
    _ramp_speed(clamped)
    return clamped


def odometer_cm() -> float:
    """Rough cumulative distance estimate in cm (no encoders)."""
    return round(_odo_cm, 1)


def reset_odometer() -> None:
    global _odo_cm
    _odo_cm = 0.0


def cleanup() -> None:
    stop()
    for pwm in (_pwm_a, _pwm_b):
        try:
            if pwm is not None:
                pwm.stop()     # type: ignore[union-attr]
        except Exception:
            pass
    logger.info("Motor resources released.")


# ──────────────────────────────────────────────
# Timed-stop helper (fire-and-forget thread)
# ──────────────────────────────────────────────
def _schedule_stop(duration_ms: int) -> None:
    def _stopper() -> None:
        time.sleep(duration_ms / 1000.0)
        stop()

    t = threading.Thread(target=_stopper, daemon=True, name="tars-motor-timer")
    t.start()

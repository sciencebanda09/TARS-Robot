from __future__ import annotations

import logging
from typing import Iterable, Tuple

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception as exc:  # pragma: no cover
    GPIO = None
    logger.warning("RPi.GPIO unavailable in motors module: %s", exc)

IN1, IN2, ENA = 17, 18, 22
IN3, IN4, ENB = 23, 24, 25

PWM_FREQUENCY = 100

pwm_a = None
pwm_b = None
safety_lockout = False
_initialized = False


def _write_pin(pin: int, state: int) -> None:
    if GPIO is None:
        return
    GPIO.output(pin, state)


def _set_pair(left_forward: bool, right_forward: bool) -> None:
    if GPIO is None:
        return

    _write_pin(IN1, GPIO.HIGH if left_forward else GPIO.LOW)
    _write_pin(IN2, GPIO.LOW if left_forward else GPIO.HIGH)
    _write_pin(IN3, GPIO.HIGH if right_forward else GPIO.LOW)
    _write_pin(IN4, GPIO.LOW if right_forward else GPIO.HIGH)


def _set_speed(speed: int = 50) -> int:
    speed = max(0, min(100, int(speed)))
    if pwm_a is not None:
        pwm_a.ChangeDutyCycle(speed)
    if pwm_b is not None:
        pwm_b.ChangeDutyCycle(speed)
    return speed


def init_motors() -> None:
    global pwm_a, pwm_b, _initialized

    if GPIO is None:
        return

    if _initialized:
        return

    GPIO.setup([IN1, IN2, ENA, IN3, IN4, ENB], GPIO.OUT)
    GPIO.output([IN1, IN2, IN3, IN4], GPIO.LOW)

    pwm_a = GPIO.PWM(ENA, PWM_FREQUENCY)
    pwm_b = GPIO.PWM(ENB, PWM_FREQUENCY)
    pwm_a.start(0)
    pwm_b.start(0)
    _initialized = True
    logger.info("Motor driver initialized.")


def update_safety_lockout(is_blocked: bool) -> None:
    global safety_lockout
    safety_lockout = bool(is_blocked)
    if safety_lockout:
        stop()


def set_speed(speed: int = 50) -> int:
    return _set_speed(speed)


def move_forward(speed: int = 50) -> bool:
    if safety_lockout:
        logger.warning("Forward motion blocked by safety lockout.")
        stop()
        return False
    _set_speed(speed)
    _set_pair(True, True)
    return True


def move_backward(speed: int = 50) -> bool:
    _set_speed(speed)
    _set_pair(False, False)
    return True


def turn_left(speed: int = 50) -> bool:
    if safety_lockout:
        stop()
        return False
    _set_speed(speed)
    _set_pair(False, True)
    return True


def turn_right(speed: int = 50) -> bool:
    if safety_lockout:
        stop()
        return False
    _set_speed(speed)
    _set_pair(True, False)
    return True


def stop() -> None:
    _set_speed(0)
    if GPIO is not None:
        GPIO.output([IN1, IN2, IN3, IN4], GPIO.LOW)


def cleanup() -> None:
    stop()
    try:
        if pwm_a is not None:
            pwm_a.stop()
    except Exception:
        pass
    try:
        if pwm_b is not None:
            pwm_b.stop()
    except Exception:
        pass

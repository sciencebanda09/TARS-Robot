from __future__ import annotations

import logging
import math
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception as exc:  # pragma: no cover
    GPIO = None
    logger.warning("RPi.GPIO unavailable in sensor module: %s", exc)

try:
    import Adafruit_DHT
except Exception as exc:  # pragma: no cover
    Adafruit_DHT = None
    logger.warning("Adafruit_DHT unavailable: %s", exc)

DHT_SENSOR = getattr(Adafruit_DHT, "DHT11", None) if Adafruit_DHT else None
DHT_PIN = 4

TRIG_PIN = 20
ECHO_PIN = 16

DISTANCE_TIMEOUT = 0.05
SPEED_OF_SOUND_CM_S = 34300.0


def init_sensors() -> None:
    if GPIO is None:
        return
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.05)


def read_environment() -> Dict[str, Optional[float]]:
    """Return temperature and humidity from the DHT sensor."""
    if Adafruit_DHT is None or DHT_SENSOR is None:
        return {"temp_c": None, "humidity": None}

    try:
        humidity, temp = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
        if temp is not None and humidity is not None:
            return {"temp_c": round(float(temp), 1), "humidity": round(float(humidity), 1)}
    except Exception as exc:
        logger.warning("DHT11 read error: %s", exc)

    return {"temp_c": None, "humidity": None}


def _pulse_distance_once(timeout: float = DISTANCE_TIMEOUT) -> float:
    if GPIO is None:
        return 999.0

    try:
        GPIO.output(TRIG_PIN, True)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, False)

        start = time.monotonic()
        pulse_start = None
        pulse_end = None

        while GPIO.input(ECHO_PIN) == 0:
            pulse_start = time.monotonic()
            if pulse_start - start > timeout:
                return 999.0

        while GPIO.input(ECHO_PIN) == 1:
            pulse_end = time.monotonic()
            if pulse_end - start > timeout:
                return 999.0

        if pulse_start is None or pulse_end is None:
            return 999.0

        elapsed = pulse_end - pulse_start
        distance = (elapsed * SPEED_OF_SOUND_CM_S) / 2.0
        if math.isnan(distance) or distance <= 0:
            return 999.0
        return round(distance, 1)
    except Exception as exc:
        logger.error("Ultrasonic sensor error: %s", exc)
        return 999.0


def read_distance(samples: int = 2) -> float:
    """Return a smoothed range estimate in cm."""
    samples = max(1, int(samples))
    readings = []
    for _ in range(samples):
        value = _pulse_distance_once()
        if value < 998.0:
            readings.append(value)
        time.sleep(0.02)

    if not readings:
        return 999.0
    return round(sum(readings) / len(readings), 1)


def read_telemetry() -> Dict[str, Optional[float]]:
    env = read_environment()
    env["distance_cm"] = read_distance()
    return env

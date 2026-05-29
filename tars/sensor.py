"""
tars/sensor.py  –  Sensor Abstraction Layer

Improvements over v1:
  • Simple 1-D Kalman filter on distance readings (removes ultrasonic jitter)
  • Rolling average for temperature / humidity (DHT11 is noisy)
  • Trend tracking: rising/falling/stable for temperature
  • Sensor health monitor: consecutive failure counter → raises alert
  • Fully mock-safe for development without hardware
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception:       # pragma: no cover
    GPIO = None

try:
    import Adafruit_DHT
    DHT_SENSOR = Adafruit_DHT.DHT11
except Exception:       # pragma: no cover
    Adafruit_DHT = None
    DHT_SENSOR   = None

# ──────────────────────────────────────────────
# Pin assignments
# ──────────────────────────────────────────────
DHT_PIN   = 4
TRIG_PIN  = 20
ECHO_PIN  = 16

# ──────────────────────────────────────────────
# Ultrasonic constants
# ──────────────────────────────────────────────
DISTANCE_TIMEOUT     = 0.05
SPEED_OF_SOUND_CM_S  = 34300.0

# ──────────────────────────────────────────────
# Kalman parameters (distance)
# ──────────────────────────────────────────────
_KF_Q = 1.0    # process noise
_KF_R = 5.0    # measurement noise (higher = trust sensor less)

# ──────────────────────────────────────────────
# Rolling-average window sizes
# ──────────────────────────────────────────────
TEMP_WINDOW = 5
DIST_WINDOW = 4

# ──────────────────────────────────────────────
# Sensor state
# ──────────────────────────────────────────────
_dist_kf_x: float = 100.0   # Kalman estimate
_dist_kf_p: float = 1.0     # Kalman uncertainty

_temp_history:   Deque[float] = deque(maxlen=TEMP_WINDOW)
_humid_history:  Deque[float] = deque(maxlen=TEMP_WINDOW)
_dist_history:   Deque[float] = deque(maxlen=DIST_WINDOW)

_dht_fail_count: int = 0
_us_fail_count:  int = 0
MAX_CONSECUTIVE_FAILS = 5


# ──────────────────────────────────────────────
# Init
# ──────────────────────────────────────────────
def init_sensors() -> None:
    if GPIO is None:
        logger.info("Sensor mock mode – no GPIO.")
        return
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.05)
    logger.info("Sensors initialised (TRIG=%d, ECHO=%d, DHT=%d).", TRIG_PIN, ECHO_PIN, DHT_PIN)


# ──────────────────────────────────────────────
# Kalman update (1-D)
# ──────────────────────────────────────────────
def _kalman_update(z: float) -> float:
    global _dist_kf_x, _dist_kf_p
    _dist_kf_p += _KF_Q
    k = _dist_kf_p / (_dist_kf_p + _KF_R)
    _dist_kf_x += k * (z - _dist_kf_x)
    _dist_kf_p *= (1.0 - k)
    return round(_dist_kf_x, 1)


# ──────────────────────────────────────────────
# Ultrasonic distance
# ──────────────────────────────────────────────
def _raw_distance_once() -> float:
    if GPIO is None:
        return 999.0
    try:
        GPIO.output(TRIG_PIN, True)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, False)

        deadline = time.monotonic() + DISTANCE_TIMEOUT
        while GPIO.input(ECHO_PIN) == 0:
            t_start = time.monotonic()
            if t_start > deadline:
                return 999.0
        while GPIO.input(ECHO_PIN) == 1:
            t_end = time.monotonic()
            if t_end > deadline:
                return 999.0

        dist = ((t_end - t_start) * SPEED_OF_SOUND_CM_S) / 2.0
        if math.isnan(dist) or dist <= 0:
            return 999.0
        return round(dist, 1)
    except Exception as exc:
        logger.debug("Ultrasonic error: %s", exc)
        return 999.0


def read_distance(samples: int = 3) -> float:
    """Return Kalman-filtered distance in cm."""
    global _us_fail_count
    readings = []
    for _ in range(max(1, samples)):
        val = _raw_distance_once()
        if val < 998.0:
            readings.append(val)
        time.sleep(0.02)

    if not readings:
        _us_fail_count += 1
        if _us_fail_count >= MAX_CONSECUTIVE_FAILS:
            logger.warning("Ultrasonic sensor: %d consecutive failures.", _us_fail_count)
        return _kalman_update(999.0)

    _us_fail_count = 0
    raw_avg = sum(readings) / len(readings)
    filtered = _kalman_update(raw_avg)
    _dist_history.append(filtered)
    return filtered


def distance_trend() -> str:
    """Return 'approaching', 'receding', or 'stable' based on recent readings."""
    if len(_dist_history) < 2:
        return "stable"
    delta = _dist_history[-1] - _dist_history[0]
    if delta < -3.0:
        return "approaching"
    if delta > 3.0:
        return "receding"
    return "stable"


# ──────────────────────────────────────────────
# DHT environment
# ──────────────────────────────────────────────
def read_environment() -> Dict[str, Optional[float]]:
    global _dht_fail_count
    if Adafruit_DHT is None or DHT_SENSOR is None:
        return {"temp_c": None, "humidity": None}

    try:
        humidity, temp = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN, retries=3)
        if temp is not None and humidity is not None:
            _dht_fail_count = 0
            _temp_history.append(float(temp))
            _humid_history.append(float(humidity))
            return {
                "temp_c":  round(sum(_temp_history) / len(_temp_history), 1),
                "humidity": round(sum(_humid_history) / len(_humid_history), 1),
            }
    except Exception as exc:
        logger.debug("DHT read error: %s", exc)

    _dht_fail_count += 1
    if _dht_fail_count >= MAX_CONSECUTIVE_FAILS:
        logger.warning("DHT sensor: %d consecutive failures.", _dht_fail_count)

    # Return last known value if available
    if _temp_history:
        return {
            "temp_c":  round(sum(_temp_history) / len(_temp_history), 1),
            "humidity": round(sum(_humid_history) / len(_humid_history), 1) if _humid_history else None,
        }
    return {"temp_c": None, "humidity": None}


def temperature_trend() -> str:
    """'rising', 'falling', or 'stable' over recent DHT samples."""
    if len(_temp_history) < 2:
        return "stable"
    delta = list(_temp_history)[-1] - list(_temp_history)[0]
    if delta > 0.5:
        return "rising"
    if delta < -0.5:
        return "falling"
    return "stable"


# ──────────────────────────────────────────────
# Combined telemetry
# ──────────────────────────────────────────────
def read_telemetry() -> Dict[str, object]:
    env = read_environment()
    env["distance_cm"]    = read_distance()        # type: ignore[assignment]
    env["dist_trend"]     = distance_trend()       # type: ignore[assignment]
    env["temp_trend"]     = temperature_trend()    # type: ignore[assignment]
    env["dht_failures"]   = _dht_fail_count        # type: ignore[assignment]
    env["us_failures"]    = _us_fail_count         # type: ignore[assignment]
    return env


def sensor_health() -> Tuple[bool, bool]:
    """Return (dht_ok, ultrasonic_ok)."""
    return (_dht_fail_count < MAX_CONSECUTIVE_FAILS,
            _us_fail_count < MAX_CONSECUTIVE_FAILS)

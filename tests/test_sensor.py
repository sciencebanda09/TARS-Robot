"""Unit tests for tars/sensor.py – no hardware required."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tars.sensor as sensor_mod


def test_read_distance_mock_returns_float():
    # GPIO is None in test environment – should return Kalman-filtered default
    d = sensor_mod.read_distance(samples=1)
    assert isinstance(d, float)


def test_kalman_smooths_spike():
    # Push several normal readings then a spike
    for _ in range(5):
        sensor_mod._kalman_update(50.0)
    smoothed = sensor_mod._kalman_update(999.0)
    # Kalman should absorb the spike – result must be well below 999
    # With KF_R=5 and 5 prior readings, the filter converges partially; 500 is a safe bound
    assert smoothed < 500.0


def test_read_environment_mock():
    env = sensor_mod.read_environment()
    assert "temp_c" in env
    assert "humidity" in env


def test_telemetry_has_expected_keys():
    t = sensor_mod.read_telemetry()
    for key in ("temp_c", "humidity", "distance_cm", "dist_trend", "temp_trend"):
        assert key in t


def test_distance_trend_stable_initially():
    sensor_mod._dist_history.clear()
    assert sensor_mod.distance_trend() == "stable"


def test_sensor_health_ok_at_start():
    sensor_mod._dht_fail_count = 0
    sensor_mod._us_fail_count  = 0
    dht_ok, us_ok = sensor_mod.sensor_health()
    assert dht_ok and us_ok

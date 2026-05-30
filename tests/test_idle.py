"""Tests for tars/idle.py – no hardware, no network."""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tars.idle as idle_mod


def test_idle_engine_fires_after_timeout(monkeypatch):
    spoken = []
    monkeypatch.setattr(idle_mod, "IDLE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(idle_mod, "IDLE_REPEAT_MIN_S", 999.0)

    engine = idle_mod.IdleBehaviourEngine(
        speak=lambda t: spoken.append(t),
        display=lambda m, s=None: None,
        sound=lambda e: None,
    )
    engine.start()
    time.sleep(0.5)
    engine.stop()
    assert len(spoken) >= 1, "Idle engine should have spoken at least once"


def test_idle_engine_reset_delays_firing(monkeypatch):
    spoken = []
    monkeypatch.setattr(idle_mod, "IDLE_TIMEOUT_S", 0.15)
    monkeypatch.setattr(idle_mod, "IDLE_REPEAT_MIN_S", 999.0)

    engine = idle_mod.IdleBehaviourEngine(
        speak=lambda t: spoken.append(t),
        display=lambda m, s=None: None,
        sound=lambda e: None,
    )
    engine.start()
    # Keep resetting
    for _ in range(5):
        time.sleep(0.05)
        engine.reset()
    engine.stop()
    assert len(spoken) == 0, "Reset should have prevented idle from firing"


def test_alert_monitor_fires_on_high_temp(monkeypatch):
    spoken = []
    monkeypatch.setattr(idle_mod, "TEMP_ALERT_C", 30.0)
    monkeypatch.setattr(idle_mod, "ALERT_POLL_S", 0.05)

    def fake_telemetry():
        return {"temp_c": 45.0, "distance_cm": 200.0}

    monitor = idle_mod.ProactiveAlertMonitor(
        speak=lambda t: spoken.append(t),
        display=lambda m, s=None: None,
        sound=lambda e: None,
        read_telemetry=fake_telemetry,
    )
    monitor.start()
    time.sleep(0.3)
    monitor.stop()
    assert any("temperature" in s.lower() or "celsius" in s.lower() for s in spoken)


def test_alert_monitor_obstacle(monkeypatch):
    spoken = []
    monkeypatch.setattr(idle_mod, "OBSTACLE_ALERT_CM", 50.0)
    monkeypatch.setattr(idle_mod, "OBSTACLE_COOLDOWN_S", 0.0)
    monkeypatch.setattr(idle_mod, "ALERT_POLL_S", 0.05)

    def fake_telemetry():
        return {"temp_c": 25.0, "distance_cm": 10.0}

    monitor = idle_mod.ProactiveAlertMonitor(
        speak=lambda t: spoken.append(t),
        display=lambda m, s=None: None,
        sound=lambda e: None,
        read_telemetry=fake_telemetry,
    )
    monitor.start()
    time.sleep(0.3)
    monitor.stop()
    assert any("obstacle" in s.lower() for s in spoken)


def test_alert_no_fire_when_normal(monkeypatch):
    spoken = []
    monkeypatch.setattr(idle_mod, "TEMP_ALERT_C", 80.0)
    monkeypatch.setattr(idle_mod, "OBSTACLE_ALERT_CM", 5.0)
    monkeypatch.setattr(idle_mod, "ALERT_POLL_S", 0.05)

    monitor = idle_mod.ProactiveAlertMonitor(
        speak=lambda t: spoken.append(t),
        display=lambda m, s=None: None,
        sound=lambda e: None,
        read_telemetry=lambda: {"temp_c": 25.0, "distance_cm": 100.0},
    )
    monitor.start()
    time.sleep(0.3)
    monitor.stop()
    assert spoken == []

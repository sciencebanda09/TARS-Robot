"""Tests for tars/pir.py – no hardware required."""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tars.pir as pir_mod


def test_starts_awake():
    pir = pir_mod.PIRWakeController()
    pir.start()
    assert pir.is_awake is True
    pir.stop()


def test_auto_sleep_after_timeout(monkeypatch):
    monkeypatch.setattr(pir_mod, "SLEEP_AFTER_S", 0.2)
    slept = []
    pir = pir_mod.PIRWakeController(on_sleep=lambda: slept.append(1))
    pir.start()
    time.sleep(0.7)
    pir.stop()
    assert pir.is_awake is False
    assert len(slept) >= 1


def test_notify_activity_prevents_sleep(monkeypatch):
    monkeypatch.setattr(pir_mod, "SLEEP_AFTER_S", 0.2)
    pir = pir_mod.PIRWakeController()
    pir.start()
    for _ in range(8):
        time.sleep(0.05)
        pir.notify_activity()
    assert pir.is_awake is True
    pir.stop()


def test_wake_callback_fires_after_sleep(monkeypatch):
    monkeypatch.setattr(pir_mod, "SLEEP_AFTER_S", 0.2)
    woke = []
    pir = pir_mod.PIRWakeController(
        on_wake=lambda: woke.append(1),
        on_sleep=lambda: None,
    )
    pir.start()
    time.sleep(0.6)          # let it sleep
    pir.notify_activity()    # simulate PIR motion
    time.sleep(0.2)
    pir.stop()
    assert len(woke) >= 1


def test_status_dict_keys():
    pir = pir_mod.PIRWakeController()
    pir.start()
    status = pir.status_dict()
    pir.stop()
    for key in ("awake", "seconds_since_motion", "sleep_after_s", "pir_pin"):
        assert key in status


def test_seconds_since_motion_increases():
    pir = pir_mod.PIRWakeController()
    pir.start()
    time.sleep(0.15)
    assert pir.seconds_since_motion() >= 0.1
    pir.stop()

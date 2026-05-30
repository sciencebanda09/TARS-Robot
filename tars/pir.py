"""
tars/pir.py  –  PIR Motion Sensor Wake System  (NEW)

Manages a passive-infrared (PIR) sensor (e.g. HC-SR501 on GPIO pin 27).

Behaviour:
  • In SLEEP mode: OLED dims, TTS muted, AI disabled, motors locked
  • PIR interrupt fires → TARS wakes, plays power-up sound, greets the person
  • After SLEEP_AFTER_S of no motion, TARS returns to sleep
  • Callbacks let main.py hook into state changes cleanly

Hardware wiring:
  HC-SR501 OUT pin → GPIO 27 (BCM)
  Sensitivity / delay trimmers on the module itself

The module is fully mock-safe: if RPi.GPIO is unavailable
(dev laptop) it stays perpetually "awake" so everything else works.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None

# ── Config ───────────────────────────────────────────────
PIR_PIN       = int(os.getenv("TARS_PIR_PIN",       "27"))
SLEEP_AFTER_S = float(os.getenv("TARS_SLEEP_AFTER", "120"))  # seconds of no motion → sleep
DEBOUNCE_MS   = int(os.getenv("TARS_PIR_DEBOUNCE",  "500"))  # ms, prevents flicker

WakeCallback  = Callable[[], None]
SleepCallback = Callable[[], None]


class PIRWakeController:
    """
    Manages robot sleep/wake via PIR interrupt.

    Usage in main.py:
        pir = PIRWakeController(
            on_wake=lambda: voice.speak("Motion detected. TARS online."),
            on_sleep=lambda: display.show_expression("neutral", "SLEEPING"),
        )
        pir.start()
        ...
        if not pir.is_awake:
            continue   # skip voice loop while sleeping
    """

    def __init__(
        self,
        on_wake:  Optional[WakeCallback]  = None,
        on_sleep: Optional[SleepCallback] = None,
    ) -> None:
        self._on_wake  = on_wake
        self._on_sleep = on_sleep

        self._awake        = True    # start awake
        self._last_motion  = time.monotonic()
        self._stop_event   = threading.Event()

        # Watchdog checks for inactivity → auto-sleep
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="tars-pir-watchdog"
        )

        self._lock = threading.Lock()

    # ── Public interface ─────────────────────────────────
    @property
    def is_awake(self) -> bool:
        return self._awake

    def start(self) -> None:
        if GPIO is not None:
            try:
                GPIO.setup(PIR_PIN, GPIO.IN)
                GPIO.add_event_detect(
                    PIR_PIN,
                    GPIO.RISING,
                    callback=self._pir_callback,
                    bouncetime=DEBOUNCE_MS,
                )
                logger.info("PIR sensor armed on GPIO %d (BCM).", PIR_PIN)
            except Exception as exc:
                logger.warning("PIR GPIO setup failed: %s – wake always active.", exc)
        else:
            logger.info("PIR mock mode – robot stays perpetually awake.")

        self._watchdog.start()

    def stop(self) -> None:
        self._stop_event.set()
        if GPIO is not None:
            try:
                GPIO.remove_event_detect(PIR_PIN)
            except Exception:
                pass

    def notify_activity(self) -> None:
        """
        Call this whenever the user speaks or TARS responds –
        resets the inactivity timer so TARS doesn't sleep mid-conversation.
        """
        self._last_motion = time.monotonic()
        if not self._awake:
            self._wake()

    # ── Internal ─────────────────────────────────────────
    def _pir_callback(self, channel: int) -> None:
        """GPIO interrupt handler – runs on GPIO event thread."""
        self._last_motion = time.monotonic()
        if not self._awake:
            self._wake()

    def _wake(self) -> None:
        with self._lock:
            if self._awake:
                return
            self._awake = True
        logger.info("PIR: motion detected – waking TARS.")
        if self._on_wake:
            try:
                self._on_wake()
            except Exception as exc:
                logger.warning("Wake callback error: %s", exc)

    def _sleep(self) -> None:
        with self._lock:
            if not self._awake:
                return
            self._awake = False
        logger.info("PIR: %.0fs of inactivity – entering sleep.", SLEEP_AFTER_S)
        if self._on_sleep:
            try:
                self._on_sleep()
            except Exception as exc:
                logger.warning("Sleep callback error: %s", exc)

    def _watchdog_loop(self) -> None:
        """Periodically check for prolonged inactivity."""
        poll_s = min(1.0, SLEEP_AFTER_S / 2)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=poll_s)
            if self._stop_event.is_set():
                break

            idle_s = time.monotonic() - self._last_motion
            if self._awake and idle_s >= SLEEP_AFTER_S:
                self._sleep()

    def seconds_since_motion(self) -> float:
        return round(time.monotonic() - self._last_motion, 1)

    def status_dict(self) -> dict:
        return {
            "awake": self._awake,
            "seconds_since_motion": self.seconds_since_motion(),
            "sleep_after_s": SLEEP_AFTER_S,
            "pir_pin": PIR_PIN,
        }

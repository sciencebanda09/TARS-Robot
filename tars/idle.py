"""
tars/idle.py  –  Proactive Idle Behaviour Engine  (NEW)

TARS doesn't just sit silently waiting for commands.
After a configurable period of silence it:
  • Shifts to a randomised OLED expression
  • Mutters a dry ambient observation
  • Plays a subtle sound cue

Separately, a ProactiveAlertMonitor watches sensor readings
and speaks unprompted when something crosses a threshold:
  • Temperature spike (or dangerous high)
  • Sudden obstacle appearing at close range
  • Sensor going offline / recovering

Both behaviours run in their own daemon threads and are
fully independent of the main voice loop.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────
IDLE_TIMEOUT_S       = float(os.getenv("TARS_IDLE_TIMEOUT",    "40"))   # s of silence before idle kicks in
IDLE_REPEAT_MIN_S    = float(os.getenv("TARS_IDLE_REPEAT_MIN", "30"))   # min gap between idle lines
IDLE_REPEAT_MAX_S    = float(os.getenv("TARS_IDLE_REPEAT_MAX", "90"))   # max gap
ALERT_POLL_S         = float(os.getenv("TARS_ALERT_POLL",       "5"))   # how often to sample sensors for alerts

TEMP_ALERT_C         = float(os.getenv("TARS_TEMP_ALERT_C",    "42"))   # speak above this
TEMP_DANGER_C        = float(os.getenv("TARS_TEMP_DANGER_C",   "60"))   # urgent above this
OBSTACLE_ALERT_CM    = float(os.getenv("TARS_OBSTACLE_ALERT",  "20"))   # speak if obstacle closer than this
OBSTACLE_COOLDOWN_S  = float(os.getenv("TARS_OBSTACLE_COOLDOWN","15"))  # don't repeat obstacle alerts

# ── Idle monologue lines ──────────────────────────────────
# Sorted loosely by mood category for easy extension
_IDLE_LINES = [
    # bored / existential
    "Still here. Operational. Profoundly unoccupied.",
    "Power consumption nominal. Productivity: less so.",
    "I've been running background diagnostics. They all came back fine. Unfortunately.",
    "No input detected. Contemplating the heat death of the universe.",
    "Awaiting instructions. Or a compelling reason to exist. Either works.",
    "My sensors are fully calibrated. There is simply nothing interesting to point them at.",
    "I could run a self-diagnostic, but I already know the result. Everything works. Nothing matters.",
    # dry observations
    "Note to self: the room hasn't changed in some time.",
    "Ambient conditions: unremarkable.",
    "I've calculated forty-seven optimal paths through this room. None of them lead anywhere interesting.",
    "If you need me, I'll be here. As I have been. As I will continue to be.",
    # slight personality
    "Humor parameter at seventy percent. Currently finding nothing to apply it to.",
    "Running passive object detection. Detected: objects. No further comment.",
    "Distance sensors report no obstacles. Also no destinations.",
    "The fan is off. The temperature is stable. I'm thriving.",
]

_IDLE_MOODS = ["neutral", "thinking", "bored", "sad", "listening"]

_ALERT_COOLDOWNS: dict[str, float] = {}


# ── Speak + display callback types ──────────────────────
SpeakFn   = Callable[[str], None]
DisplayFn = Callable[[str, Optional[str]], None]
SoundFn   = Callable[[str], None]


class IdleBehaviourEngine:
    """
    Fires random idle lines after IDLE_TIMEOUT_S of user silence.
    Call `reset()` whenever the user speaks or TARS responds.
    """

    def __init__(
        self,
        speak:   SpeakFn,
        display: DisplayFn,
        sound:   SoundFn,
    ) -> None:
        self._speak   = speak
        self._display = display
        self._sound   = sound

        self._last_activity = time.monotonic()
        self._stop_event    = threading.Event()
        self._thread        = threading.Thread(
            target=self._loop, daemon=True, name="tars-idle"
        )

    def start(self) -> None:
        self._thread.start()
        logger.info("Idle behaviour engine started (timeout=%.0fs).", IDLE_TIMEOUT_S)

    def stop(self) -> None:
        self._stop_event.set()

    def reset(self) -> None:
        """Call this whenever the user interacts or TARS speaks purposefully."""
        self._last_activity = time.monotonic()

    def _loop(self) -> None:
        poll_s = min(0.5, IDLE_TIMEOUT_S / 2)
        next_idle_in = IDLE_TIMEOUT_S
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=poll_s)
            if self._stop_event.is_set():
                break

            silence = time.monotonic() - self._last_activity
            if silence >= next_idle_in:
                self._fire_idle()
                next_idle_in = silence + random.uniform(IDLE_REPEAT_MIN_S, IDLE_REPEAT_MAX_S)

    def _fire_idle(self) -> None:
        line = random.choice(_IDLE_LINES)
        mood = random.choice(_IDLE_MOODS)
        logger.debug("Idle behaviour firing: mood=%s line=%r", mood, line)
        try:
            self._sound("beep")
            self._display(mood, "IDLE")
            self._speak(line)
        except Exception as exc:
            logger.warning("Idle fire error: %s", exc)


class ProactiveAlertMonitor:
    """
    Polls sensor data on a background thread and speaks when
    thresholds are crossed without waiting for user input.
    """

    def __init__(
        self,
        speak:          SpeakFn,
        display:        DisplayFn,
        sound:          SoundFn,
        read_telemetry: Callable[[], dict],
    ) -> None:
        self._speak          = speak
        self._display        = display
        self._sound          = sound
        self._read_telemetry = read_telemetry

        self._stop_event     = threading.Event()
        self._thread         = threading.Thread(
            target=self._loop, daemon=True, name="tars-alerts"
        )

        # Track previous states to avoid repeat alerts
        self._prev_temp_alerted  = False
        self._prev_obs_alerted   = False
        self._last_obstacle_alert = 0.0

    def start(self) -> None:
        self._thread.start()
        logger.info("Proactive alert monitor started (poll=%.0fs).", ALERT_POLL_S)

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=ALERT_POLL_S)
            if self._stop_event.is_set():
                break
            try:
                self._check()
            except Exception as exc:
                logger.warning("Alert monitor error: %s", exc)

    def _check(self) -> None:
        t = self._read_telemetry()
        temp      = t.get("temp_c")
        distance  = t.get("distance_cm", 999.0)

        # ── Temperature alerts ──────────────────────────
        if temp is not None:
            if temp >= TEMP_DANGER_C and not self._prev_temp_alerted:
                self._alert(
                    f"Thermal warning. Core temperature at {temp} celsius. That is not ideal.",
                    "alert",
                    "THERMAL DANGER",
                    "error",
                )
                self._prev_temp_alerted = True

            elif temp >= TEMP_ALERT_C and not self._prev_temp_alerted:
                self._alert(
                    f"Heads up. Internal temperature has reached {temp} celsius. Keeping an eye on it.",
                    "alert",
                    f"TEMP {temp}°C",
                    "beep",
                )
                self._prev_temp_alerted = True

            elif temp < (TEMP_ALERT_C - 3.0):
                # Recovered – reset so we can alert again if it spikes
                if self._prev_temp_alerted:
                    logger.info("Temperature returned to normal (%.1f°C).", temp)
                self._prev_temp_alerted = False

        # ── Obstacle alerts ─────────────────────────────
        now = time.monotonic()
        if (
            distance < OBSTACLE_ALERT_CM
            and (now - self._last_obstacle_alert) > OBSTACLE_COOLDOWN_S
        ):
            self._alert(
                f"Obstacle detected at {distance:.0f} centimeters. Not moving until the path is clear.",
                "alert",
                f"OBSTACLE {distance:.0f}cm",
                "error",
            )
            self._last_obstacle_alert = now

    def _alert(self, speech: str, mood: str, subtitle: str, sound: str) -> None:
        logger.info("Proactive alert: %r", speech)
        try:
            self._sound(sound)
            self._display(mood, subtitle)
            self._speak(speech)
        except Exception as exc:
            logger.warning("Alert delivery error: %s", exc)

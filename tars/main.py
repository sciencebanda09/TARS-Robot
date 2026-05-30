"""
tars/main.py  –  TARS System Orchestrator  (v3 – Idle + PIR + Dashboard)

New in v3:
  • IdleBehaviourEngine: dry ambient monologue after silence
  • ProactiveAlertMonitor: unprompted thermal + obstacle alerts
  • PIRWakeController: sleep/wake on motion, saves CPU
  • Web dashboard on :8080 with live telemetry, console, motion pad
  • Command queue: dashboard POST /api/command feeds the same loop as voice
  • dashboard.update_state() called each cycle with fresh sensor + system data
"""

from __future__ import annotations

import json
import logging
import queue
import signal
import sys
import threading
import time
from typing import Any, Dict, Optional

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None

import tars.ai        as ai
import tars.dashboard as dashboard
import tars.display   as display
import tars.fan       as fan
import tars.idle      as idle_mod
import tars.motors    as motors
import tars.pir       as pir_mod
import tars.sensor    as sensor
import tars.sounds    as sounds
import tars.voice     as voice
from tars.memory import (
    append_message,
    close_episode,
    log_event,
    memory,
    save_memory,
    update_setting,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tars.main")

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
SAFE_DISTANCE_CM     = 25.0
COOLING_THRESHOLD_C  = 36.0
SAFETY_HZ            = 10
THERMAL_TICK_SECONDS = 8.0
SESSION_START        = time.monotonic()

# ──────────────────────────────────────────────
# Shared command queue (voice + dashboard both feed this)
# ──────────────────────────────────────────────
_cmd_queue: queue.Queue[str] = queue.Queue()

# ──────────────────────────────────────────────
# Shutdown
# ──────────────────────────────────────────────
_shutdown_event = threading.Event()


def _handle_signal(sig: int, frame: object) -> None:
    logger.warning("Signal %d received – initiating shutdown.", sig)
    _shutdown_event.set()


signal.signal(signal.SIGTERM, _handle_signal)


# ──────────────────────────────────────────────
# Tool helpers
# ──────────────────────────────────────────────
def _tool_args(tool_call: Any) -> Dict[str, Any]:
    raw = getattr(getattr(tool_call, "function", None), "arguments", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _tool_name(tool_call: Any) -> str:
    return getattr(getattr(tool_call, "function", None), "name", "")


def execute_tool(tool_call: Any) -> str:
    name = _tool_name(tool_call)
    args = _tool_args(tool_call)
    logger.info("Tool: %s  args=%s", name, args)

    if name == "control_motion":
        direction   = args.get("direction")
        speed       = int(args.get("speed", 50))
        duration_ms = int(args.get("duration_ms", 0))
        if direction == "forward":
            ok = motors.move_forward(speed, duration_ms)
            return "[OK: Moving forward]" if ok else "[BLOCKED: Obstacle]"
        if direction == "backward":
            motors.move_backward(speed, duration_ms); return "[OK: Reversing]"
        if direction == "left":
            ok = motors.turn_left(speed, duration_ms)
            return "[OK: Turning left]" if ok else "[BLOCKED: Safety lockout]"
        if direction == "right":
            ok = motors.turn_right(speed, duration_ms)
            return "[OK: Turning right]" if ok else "[BLOCKED: Safety lockout]"
        if direction == "stop":
            motors.stop(); return "[OK: Motion stopped]"
        return "[ERR: Unknown direction]"

    if name == "adjust_parameters":
        param = args.get("param"); level = int(args.get("level", 50))
        key_map = {"humor":"humor_setting","honesty":"honesty_setting","verbosity":"verbosity_setting"}
        if param in key_map:
            new_val = update_setting(memory, key_map[param], level)
            save_memory(memory)
            return f"[OK: {param.capitalize()} → {new_val}%]"
        return "[ERR: Unknown parameter]"

    if name == "read_telemetry":
        t = sensor.read_telemetry()
        dht_ok, us_ok = sensor.sensor_health()
        return (
            f"[Telemetry | Temp={t.get('temp_c')}°C ({t.get('temp_trend')}) | "
            f"Humidity={t.get('humidity')}% | "
            f"Distance={t.get('distance_cm')}cm ({t.get('dist_trend')}) | "
            f"DHT={'OK' if dht_ok else 'DEGRADED'} US={'OK' if us_ok else 'DEGRADED'}]"
        )

    if name == "set_display_expression":
        mood = args.get("mood", "neutral"); subtitle = args.get("subtitle")
        display.show_expression(mood, subtitle); memory["mood"] = mood
        return f"[OK: Display → '{mood}']"

    if name == "play_sound_effect":
        effect = args.get("effect", "beep"); sounds.play_effect(effect)
        return f"[OK: Sound '{effect}' queued]"

    return "[ERR: Unknown tool]"


# ──────────────────────────────────────────────
# Motion callback for dashboard
# ──────────────────────────────────────────────
def _dashboard_motion(direction: str, speed: int) -> None:
    if direction == "forward":   motors.move_forward(speed)
    elif direction == "backward": motors.move_backward(speed)
    elif direction == "left":    motors.turn_left(speed)
    elif direction == "right":   motors.turn_right(speed)
    elif direction == "stop":    motors.stop()


# ──────────────────────────────────────────────
# Dashboard state push
# ──────────────────────────────────────────────
def _push_dashboard_state() -> None:
    t = sensor.read_telemetry()
    dashboard.update_state({
        "temp_c":       t.get("temp_c"),
        "humidity":     t.get("humidity"),
        "distance_cm":  t.get("distance_cm"),
        "temp_trend":   t.get("temp_trend"),
        "dist_trend":   t.get("dist_trend"),
        "fan_on":       fan.is_fan_on(),
        "pir_awake":    _pir.is_awake if _pir else True,
    })


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────
_pir:   Optional[pir_mod.PIRWakeController]  = None
_idle:  Optional[idle_mod.IdleBehaviourEngine] = None
_alerts: Optional[idle_mod.ProactiveAlertMonitor] = None


def bootstrap() -> None:
    global _pir, _idle, _alerts

    logger.info("=" * 60)
    logger.info("  T.A.R.S  v3  –  Tactical Assistance & Response System")
    logger.info("  Humor:%d%%  Honesty:%d%%  Verbosity:%d%%",
                memory.get("humor_setting", 70),
                memory.get("honesty_setting", 90),
                memory.get("verbosity_setting", 50))
    logger.info("=" * 60)

    if GPIO is not None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    motors.init_motors()
    sensor.init_sensors()
    fan.init_fan()
    display.init_display()
    voice.init_voice()
    sounds.play_effect("power_up")

    # ── Dashboard ────────────────────────────────────────
    dashboard.configure(
        memory=memory,
        command_queue=_cmd_queue,
        speak_fn=voice.speak,
        motion_fn=_dashboard_motion,
    )
    dashboard.start_dashboard()

    # ── PIR wake controller ──────────────────────────────
    _pir = pir_mod.PIRWakeController(
        on_wake=_on_wake,
        on_sleep=_on_sleep,
    )
    _pir.start()

    # ── Idle engine ──────────────────────────────────────
    _idle = idle_mod.IdleBehaviourEngine(
        speak=voice.speak,
        display=display.show_expression,
        sound=sounds.play_effect,
    )
    _idle.start()

    # ── Proactive alert monitor ──────────────────────────
    _alerts = idle_mod.ProactiveAlertMonitor(
        speak=voice.speak,
        display=display.show_expression,
        sound=sounds.play_effect,
        read_telemetry=sensor.read_telemetry,
    )
    _alerts.start()

    display.show_expression("neutral", "SYSTEM ONLINE")
    voice.speak(
        "TARS online. Dashboard active on port eight zero eight zero. "
        f"Humor at {memory.get('humor_setting', 70)} percent.",
        blocking=False,
    )
    log_event(memory, "System v3 bootstrapped.", "info")


# ── PIR callbacks ─────────────────────────────────────────
def _on_wake() -> None:
    display.show_expression("listening", "MOTION DETECTED")
    sounds.play_effect("power_up")
    voice.speak("Motion detected. TARS online.")
    if _idle:
        _idle.reset()


def _on_sleep() -> None:
    display.show_expression("neutral", "SLEEPING")
    sounds.play_effect("beep")
    logger.info("TARS entering sleep mode.")


# ──────────────────────────────────────────────
# Safety monitor thread
# ──────────────────────────────────────────────
def _safety_monitor_loop() -> None:
    last_thermal = 0.0
    last_dashboard_push = 0.0

    while not _shutdown_event.is_set():
        tick = time.monotonic()

        dist = sensor.read_distance(samples=2)
        if dist < SAFE_DISTANCE_CM:
            motors.update_safety_lockout(True)
            display.show_expression("alert", f"OBSTACLE {dist:.0f}cm")
        else:
            motors.update_safety_lockout(False)

        if (tick - last_thermal) >= THERMAL_TICK_SECONDS:
            env = sensor.read_environment()
            fan.smart_cool(env.get("temp_c"), threshold=COOLING_THRESHOLD_C)
            last_thermal = tick

        if (tick - last_dashboard_push) >= 2.0:
            try:
                _push_dashboard_state()
            except Exception:
                pass
            last_dashboard_push = tick

        elapsed = time.monotonic() - tick
        _shutdown_event.wait(timeout=max(0.0, (1.0 / SAFETY_HZ) - elapsed))


# ──────────────────────────────────────────────
# Direct command shortcuts
# ──────────────────────────────────────────────
def _handle_direct(command: str) -> bool:
    cmd = command.strip().lower()

    if cmd in {"stop", "halt", "freeze"}:
        motors.stop()
        display.show_expression("alert", "MOTION STOPPED")
        voice.speak("Motion stopped.")
        return True

    if cmd in {"status", "telemetry", "report"}:
        t = sensor.read_telemetry()
        voice.speak(
            f"Temperature {t.get('temp_c')} celsius, "
            f"humidity {t.get('humidity')} percent, "
            f"distance {t.get('distance_cm')} centimeters, trend {t.get('dist_trend')}."
        )
        return True

    if "volume up" in cmd:
        voice.set_volume(0.95); voice.speak("Volume increased."); return True

    if "volume down" in cmd:
        voice.set_volume(0.5); voice.speak("Volume decreased."); return True

    if cmd in {"sleep", "go to sleep"}:
        if _pir: _pir._sleep()
        return True

    if cmd in {"wake up", "wake"}:
        if _pir: _pir.notify_activity()
        return True

    return False


# ──────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────
def _get_next_command() -> Optional[str]:
    """
    Pull the next command from either:
      1. The dashboard command queue (non-blocking check first)
      2. The voice listener
    """
    # Check dashboard queue first (non-blocking)
    try:
        return _cmd_queue.get_nowait()
    except queue.Empty:
        pass

    # If PIR says sleeping, don't bother listening
    if _pir and not _pir.is_awake:
        time.sleep(0.2)
        return None

    return voice.listen_command()


def main() -> None:
    bootstrap()

    safety_thread = threading.Thread(
        target=_safety_monitor_loop, daemon=True, name="tars-safety"
    )
    safety_thread.start()

    try:
        while not _shutdown_event.is_set():
            command = _get_next_command()
            if not command:
                continue

            # Any activity resets idle timer and PIR inactivity
            if _idle: _idle.reset()
            if _pir:  _pir.notify_activity()

            if _handle_direct(command):
                continue

            append_message(memory, "user", command)

            # ── First AI call ──────────────────────────────
            text_resp, tool_calls = ai.generate_tars_response(
                user_input=command,
                history=memory.get("conversation_history", []),
                humor=memory.get("humor_setting", 70),
                honesty=memory.get("honesty_setting", 90),
                verbosity=memory.get("verbosity_setting", 50),
            )

            # ── Execute tools ──────────────────────────────
            if tool_calls:
                display.show_expression("thinking", "EXECUTING")
                sounds.play_effect("thinking")
                for call in tool_calls:
                    result = execute_tool(call)
                    append_message(memory, "tool", result, name=_tool_name(call))
                    logger.info("Tool result: %s", result)

                text_resp, _ = ai.generate_tars_response(
                    user_input=command,
                    history=memory.get("conversation_history", []),
                    humor=memory.get("humor_setting", 70),
                    honesty=memory.get("honesty_setting", 90),
                    verbosity=memory.get("verbosity_setting", 50),
                )

            # ── Respond ────────────────────────────────────
            if text_resp:
                emotion = ai.get_emotion_state()
                display.show_expression(emotion.mood, "RESPONDING")
                append_message(memory, "assistant", text_resp)
                save_memory(memory)
                sounds.play_effect("beep")
                voice.speak(text_resp)
                close_episode(memory, f"User: {command[:80]} | TARS: {text_resp[:80]}")
                save_memory(memory)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt.")
    except Exception as exc:
        logger.exception("Runtime error: %s", exc)
    finally:
        _shutdown_event.set()
        _teardown()


# ──────────────────────────────────────────────
# Clean shutdown
# ──────────────────────────────────────────────
def _teardown() -> None:
    logger.warning("Teardown initiated...")

    if _idle:   _idle.stop()
    if _alerts: _alerts.stop()
    if _pir:    _pir.stop()
    dashboard.stop_dashboard()

    sounds.play_effect("power_down")
    voice.speak("Shutting down. It was a privilege.", blocking=True)

    elapsed = round(time.monotonic() - SESSION_START)
    memory["uptime_seconds"] = memory.get("uptime_seconds", 0) + elapsed
    log_event(memory, f"Clean shutdown after {elapsed}s.", "info")

    try:
        save_memory(memory)
    except Exception as exc:
        logger.error("Final memory save failed: %s", exc)

    motors.cleanup()
    fan.set_fan_state(False, force=True)
    voice.shutdown_voice()

    if GPIO is not None:
        GPIO.cleanup()

    logger.info("TARS offline. Session: %ds.", elapsed)
    sys.exit(0)


if __name__ == "__main__":
    main()

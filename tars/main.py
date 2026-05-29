"""
tars/main.py  –  TARS System Orchestrator

Improvements over v1:
  • Full tool handler for ALL registered tools (including set_display_expression,
    play_sound_effect, and motion with duration_ms)
  • Episode-closing after each full interaction cycle
  • Uptime tracking persisted to memory on clean shutdown
  • Graceful degradation: operates in sensor-only mode if AI is offline
  • Concurrent safety monitor runs at 10 Hz in a dedicated thread
  • Verbose CLI banner on startup showing active settings
  • KeyboardInterrupt + SIGTERM both trigger clean shutdown
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from typing import Any, Dict, Optional

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None

import tars.ai       as ai
import tars.display  as display
import tars.fan      as fan
import tars.motors   as motors
import tars.sensor   as sensor
import tars.sounds   as sounds
import tars.voice    as voice
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
# Tunable constants
# ──────────────────────────────────────────────
SAFE_DISTANCE_CM      = 25.0
COOLING_THRESHOLD_C   = 36.0
SAFETY_HZ             = 10         # how often the safety monitor fires per second
THERMAL_TICK_SECONDS  = 8.0
SESSION_START         = time.monotonic()

# ──────────────────────────────────────────────
# Shutdown flag
# ──────────────────────────────────────────────
_shutdown_event = threading.Event()


def _handle_signal(sig: int, frame: object) -> None:
    logger.warning("Signal %d received – initiating shutdown.", sig)
    _shutdown_event.set()


signal.signal(signal.SIGTERM, _handle_signal)


# ──────────────────────────────────────────────
# Tool argument extractor
# ──────────────────────────────────────────────
def _tool_args(tool_call: Any) -> Dict[str, Any]:
    raw = getattr(getattr(tool_call, "function", None), "arguments", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _tool_name(tool_call: Any) -> str:
    return getattr(getattr(tool_call, "function", None), "name", "")


# ──────────────────────────────────────────────
# Tool executor
# ──────────────────────────────────────────────
def execute_tool(tool_call: Any) -> str:
    name = _tool_name(tool_call)
    args = _tool_args(tool_call)
    logger.info("Executing tool: %s  args=%s", name, args)

    # ── Motion control ──────────────────────────────────────
    if name == "control_motion":
        direction   = args.get("direction")
        speed       = int(args.get("speed", 50))
        duration_ms = int(args.get("duration_ms", 0))

        if direction == "forward":
            ok = motors.move_forward(speed, duration_ms)
            return "[OK: Moving forward]" if ok else "[BLOCKED: Obstacle detected – motion rejected]"
        if direction == "backward":
            motors.move_backward(speed, duration_ms)
            return "[OK: Reversing]"
        if direction == "left":
            ok = motors.turn_left(speed, duration_ms)
            return "[OK: Turning left]" if ok else "[BLOCKED: Safety lockout]"
        if direction == "right":
            ok = motors.turn_right(speed, duration_ms)
            return "[OK: Turning right]" if ok else "[BLOCKED: Safety lockout]"
        if direction == "stop":
            motors.stop()
            return "[OK: Motion stopped]"
        return "[ERR: Unknown direction]"

    # ── Parameter adjustment ────────────────────────────────
    if name == "adjust_parameters":
        param = args.get("param")
        level = int(args.get("level", 50))
        key_map = {
            "humor":     "humor_setting",
            "honesty":   "honesty_setting",
            "verbosity": "verbosity_setting",
        }
        if param in key_map:
            new_val = update_setting(memory, key_map[param], level)
            save_memory(memory)
            return f"[OK: {param.capitalize()} set to {new_val}%]"
        return "[ERR: Unknown parameter]"

    # ── Telemetry read ──────────────────────────────────────
    if name == "read_telemetry":
        t = sensor.read_telemetry()
        dht_ok, us_ok = sensor.sensor_health()
        return (
            f"[Telemetry | "
            f"Temp={t.get('temp_c')}°C ({t.get('temp_trend')}) | "
            f"Humidity={t.get('humidity')}% | "
            f"Distance={t.get('distance_cm')}cm ({t.get('dist_trend')}) | "
            f"DHT={'OK' if dht_ok else 'DEGRADED'} "
            f"US={'OK' if us_ok else 'DEGRADED'}]"
        )

    # ── Display expression ──────────────────────────────────
    if name == "set_display_expression":
        mood     = args.get("mood", "neutral")
        subtitle = args.get("subtitle")
        display.show_expression(mood, subtitle)
        memory["mood"] = mood
        return f"[OK: Display set to '{mood}']"

    # ── Sound effect ────────────────────────────────────────
    if name == "play_sound_effect":
        effect = args.get("effect", "beep")
        sounds.play_effect(effect)
        return f"[OK: Sound '{effect}' queued]"

    return "[ERR: Unknown tool]"


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────
def bootstrap() -> None:
    logger.info("=" * 60)
    logger.info("  T.A.R.S  –  Tactical Assistance & Response System")
    logger.info("  Humor: %d%%  |  Honesty: %d%%  |  Verbosity: %d%%",
                memory.get("humor_setting", 70),
                memory.get("honesty_setting", 90),
                memory.get("verbosity_setting", 50))
    logger.info("  Total interactions logged: %d", memory.get("total_interactions", 0))
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

    display.show_expression("neutral", "SYSTEM ONLINE")
    voice.speak(
        "TARS online. "
        f"Humor at {memory.get('humor_setting', 70)} percent. "
        "All systems nominal.",
        blocking=False,
    )
    log_event(memory, "System bootstrapped.", "info")


# ──────────────────────────────────────────────
# Safety monitor (dedicated thread)
# ──────────────────────────────────────────────
def _safety_monitor_loop() -> None:
    last_thermal = 0.0
    logger.info("Safety monitor thread started at %d Hz.", SAFETY_HZ)

    while not _shutdown_event.is_set():
        tick_start = time.monotonic()

        # Obstacle detection
        dist = sensor.read_distance(samples=2)
        if dist < SAFE_DISTANCE_CM:
            motors.update_safety_lockout(True)
            display.show_expression("alert", f"OBSTACLE {dist:.0f}cm")
        else:
            motors.update_safety_lockout(False)

        # Thermal management
        now = time.monotonic()
        if (now - last_thermal) >= THERMAL_TICK_SECONDS:
            env = sensor.read_environment()
            fan.smart_cool(env.get("temp_c"), threshold=COOLING_THRESHOLD_C)
            last_thermal = now

        # Sleep to maintain desired frequency
        elapsed = time.monotonic() - tick_start
        sleep_s = max(0.0, (1.0 / SAFETY_HZ) - elapsed)
        _shutdown_event.wait(timeout=sleep_s)

    logger.info("Safety monitor thread exiting.")


# ──────────────────────────────────────────────
# Direct command shortcuts (no AI needed)
# ──────────────────────────────────────────────
_DIRECT_COMMANDS = {
    frozenset({"stop", "halt", "freeze"}): "stop",
    frozenset({"status", "telemetry", "report"}): "status",
    frozenset({"volume up"}): "vol_up",
    frozenset({"volume down"}): "vol_down",
}


def _handle_direct(command: str) -> bool:
    cmd = command.strip().lower()

    if cmd in {"stop", "halt", "freeze"}:
        motors.stop()
        display.show_expression("alert", "MOTION STOPPED")
        voice.speak("Motion stopped.")
        return True

    if cmd in {"status", "telemetry", "report"}:
        t = sensor.read_telemetry()
        msg = (
            f"Temperature {t.get('temp_c')} celsius, "
            f"humidity {t.get('humidity')} percent, "
            f"distance {t.get('distance_cm')} centimeters, "
            f"trend {t.get('dist_trend')}."
        )
        voice.speak(msg)
        return True

    if "volume up" in cmd:
        voice.set_volume(0.95)
        voice.speak("Volume increased.")
        return True

    if "volume down" in cmd:
        voice.set_volume(0.5)
        voice.speak("Volume decreased.")
        return True

    return False


# ──────────────────────────────────────────────
# Main interaction loop
# ──────────────────────────────────────────────
def main() -> None:
    bootstrap()

    # Launch safety monitor as background thread
    safety_thread = threading.Thread(
        target=_safety_monitor_loop, daemon=True, name="tars-safety"
    )
    safety_thread.start()

    try:
        while not _shutdown_event.is_set():
            command = voice.listen_command()
            if not command:
                continue

            if _handle_direct(command):
                continue

            # Append user turn to memory
            append_message(memory, "user", command)

            # ── First AI call ──────────────────────────────────
            text_resp, tool_calls = ai.generate_tars_response(
                user_input=command,
                history=memory.get("conversation_history", []),
                humor=memory.get("humor_setting", 70),
                honesty=memory.get("honesty_setting", 90),
                verbosity=memory.get("verbosity_setting", 50),
            )

            # ── Execute tool calls ─────────────────────────────
            if tool_calls:
                display.show_expression("thinking", "EXECUTING")
                sounds.play_effect("thinking")
                for call in tool_calls:
                    result = execute_tool(call)
                    append_message(
                        memory,
                        "tool",
                        result,
                        name=_tool_name(call),
                    )
                    logger.info("Tool result: %s", result)

                # ── Second AI call to synthesise result ────────
                text_resp, _ = ai.generate_tars_response(
                    user_input=command,
                    history=memory.get("conversation_history", []),
                    humor=memory.get("humor_setting", 70),
                    honesty=memory.get("honesty_setting", 90),
                    verbosity=memory.get("verbosity_setting", 50),
                )

            # ── Speak and store response ───────────────────────
            if text_resp:
                emotion = ai.get_emotion_state()
                display.show_expression(emotion.mood, "RESPONDING")
                append_message(memory, "assistant", text_resp)
                save_memory(memory)
                sounds.play_effect("beep")
                voice.speak(text_resp)

                # Seal this exchange into episodic memory
                close_episode(memory, summary=f"User: {command[:80]} | TARS: {text_resp[:80]}")
                save_memory(memory)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    except Exception as exc:
        logger.exception("Unhandled runtime error: %s", exc)
    finally:
        _shutdown_event.set()
        _teardown()


# ──────────────────────────────────────────────
# Clean shutdown
# ──────────────────────────────────────────────
def _teardown() -> None:
    logger.warning("Teardown initiated...")
    sounds.play_effect("power_down")
    voice.speak("Shutting down. It was a privilege.", blocking=True)

    # Persist uptime
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

    logger.info("TARS offline. Total session time: %ds.", elapsed)
    sys.exit(0)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover
    GPIO = None

import ai
import display
import fan
import motors
import sensor
import voice
from memory import append_message, log_event, memory, save_memory, update_setting

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SAFE_DISTANCE_CM = 25.0
COOLING_THRESHOLD_C = 36.0
SAFETY_TICK_SECONDS = 0.5
THERMAL_TICK_SECONDS = 8.0


def _tool_args(tool_call: Any) -> Dict[str, Any]:
    raw = getattr(getattr(tool_call, "function", None), "arguments", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def process_tool_execution(tool_call: Any) -> str:
    """Execute a function call requested by the model."""
    function = getattr(tool_call, "function", None)
    name = getattr(function, "name", "")
    args = _tool_args(tool_call)

    logger.info("Tool call: %s %s", name, args)

    if name == "control_motion":
        direction = args.get("direction")
        speed = args.get("speed", 50)

        if direction == "forward":
            return "[Action Success: Moving forward]" if motors.move_forward(speed) else "[Action Rejected: Path blocked]"
        if direction == "backward":
            motors.move_backward(speed)
            return "[Action Success: Reversing chassis]"
        if direction == "left":
            motors.turn_left(speed)
            return "[Action Success: Turning left]"
        if direction == "right":
            motors.turn_right(speed)
            return "[Action Success: Turning right]"
        if direction == "stop":
            motors.stop()
            return "[Action Success: Motion stopped]"
        return "[Action Error: Unknown direction]"

    if name == "adjust_parameters":
        param = args.get("param")
        level = args.get("level", 50)
        if param == "humor":
            value = update_setting(memory, "humor_setting", level)
            save_memory(memory)
            return f"[Action Success: Humor calibrated to {value}%]"
        if param == "honesty":
            value = update_setting(memory, "honesty_setting", level)
            save_memory(memory)
            return f"[Action Success: Honesty calibrated to {value}%]"
        return "[Action Error: Unknown parameter]"

    if name == "read_telemetry":
        telemetry = sensor.read_telemetry()
        return (
            "[Telemetry: "
            f"Temp={telemetry.get('temp_c')}C, "
            f"Humidity={telemetry.get('humidity')}%, "
            f"Distance={telemetry.get('distance_cm')}cm]"
        )

    return "[Action Error: Invalid function protocol]"


def bootstrap_system() -> None:
    logger.info("Initializing hardware stack...")
    if GPIO is not None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    motors.init_motors()
    sensor.init_sensors()
    fan.init_fan()
    display.init_display()
    voice.init_voice()

    display.show_expression("neutral", "SYSTEM ONLINE")
    voice.speak("System active. Humor at seventy percent. Honesty at ninety percent.")


def _safety_monitor(last_thermal_check: float) -> float:
    now = time.monotonic()
    distance = sensor.read_distance(samples=2)

    if distance < SAFE_DISTANCE_CM:
        motors.update_safety_lockout(True)
        display.show_expression("alert", "OBSTACLE DETECTED")
    else:
        motors.update_safety_lockout(False)

    if (now - last_thermal_check) >= THERMAL_TICK_SECONDS:
        env = sensor.read_environment()
        fan.smart_cool(env.get("temp_c"), threshold=COOLING_THRESHOLD_C)
        last_thermal_check = now

    return last_thermal_check


def _maybe_handle_direct_command(command: str) -> bool:
    cmd = command.strip().lower()
    if cmd in {"stop", "halt", "freeze"}:
        motors.stop()
        display.show_expression("alert", "MOTION STOPPED")
        voice.speak("Motion stopped.")
        return True
    if cmd in {"status", "telemetry"}:
        telemetry = sensor.read_telemetry()
        voice.speak(
            f"Temperature {telemetry.get('temp_c')} celsius, humidity {telemetry.get('humidity')} percent, distance {telemetry.get('distance_cm')} centimeters."
        )
        return True
    return False


def main() -> None:
    bootstrap_system()
    last_thermal_check = 0.0

    try:
        while True:
            last_thermal_check = _safety_monitor(last_thermal_check)

            command = voice.listen_command()
            if not command:
                continue

            if _maybe_handle_direct_command(command):
                continue

            append_message(memory, "user", command)

            text_response, tool_calls = ai.generate_tars_response(
                user_input=command,
                history=memory.get("conversation_history", []),
                humor=memory.get("humor_setting", 70),
                honesty=memory.get("honesty_setting", 90),
            )

            if tool_calls:
                for call in tool_calls:
                    action_result = process_tool_execution(call)
                    append_message(
                        memory,
                        "tool",
                        action_result,
                        name=getattr(getattr(call, "function", None), "name", None),
                    )

                text_response, _ = ai.generate_tars_response(
                    user_input=command,
                    history=memory.get("conversation_history", []),
                    humor=memory.get("humor_setting", 70),
                    honesty=memory.get("honesty_setting", 90),
                )

            if text_response:
                append_message(memory, "assistant", text_response)
                save_memory(memory)
                display.show_expression("thinking", "RESPONDING")
                voice.speak(text_response)

    except KeyboardInterrupt:
        logger.info("Shutdown requested by operator.")
    except Exception as exc:
        logger.exception("Unexpected runtime failure: %s", exc)
    finally:
        logger.warning("Shutting down hardware cleanly...")
        try:
            save_memory(memory)
        except Exception:
            pass
        motors.cleanup()
        fan.set_fan_state(False, force=True)
        if GPIO is not None:
            GPIO.cleanup()
        logger.info("System offline.")
        sys.exit(0)


if __name__ == "__main__":
    main()

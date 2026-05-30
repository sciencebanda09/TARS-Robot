"""
tars/ai.py  –  TARS Language-Model Brain

Improvements over v1:
  • Structured emotional state influences system prompt dynamically
  • Retry logic with exponential back-off on transient API failures
  • Confidence scoring appended to every response
  • Wake-word filtering so TARS ignores ambient chatter
  • Context compression: summarise old history to stay under token budget
  • Full type annotations throughout
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)
load_dotenv()

# ──────────────────────────────────────────────
# Client initialisation
# ──────────────────────────────────────────────
_client: Optional[OpenAI] = None
try:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        _client = OpenAI(api_key=api_key)
    else:
        logger.warning("OPENAI_API_KEY is missing – AI responses will be offline.")
except Exception as exc:
    logger.critical("OpenAI client failed to initialise: %s", exc)

MODEL          = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_HISTORY    = int(os.getenv("TARS_MODEL_HISTORY_WINDOW", "12"))
MAX_RETRIES    = int(os.getenv("TARS_AI_MAX_RETRIES", "3"))
WAKE_WORD      = os.getenv("TARS_WAKE_WORD", "tars").lower()
REQUIRE_WAKE   = os.getenv("TARS_REQUIRE_WAKE_WORD", "false").lower() == "true"

# ──────────────────────────────────────────────
# Tool schema
# ──────────────────────────────────────────────
TARS_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "control_motion",
        "description": (
            "Drive the robot chassis. Use 'stop' immediately if the user sounds alarmed. "
            "Speed 0-100 (default 50)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "backward", "left", "right", "stop"],
                },
                "speed": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 50,
                },
                "duration_ms": {
                    "type": "integer",
                    "description": "Optional: how long to drive in milliseconds before auto-stopping.",
                    "minimum": 0,
                    "maximum": 10000,
                    "default": 0,
                },
            },
            "required": ["direction"],
        },
    },
    {
        "type": "function",
        "name": "adjust_parameters",
        "description": "Update TARS personality parameters. Explain the change after doing it.",
        "parameters": {
            "type": "object",
            "properties": {
                "param": {"type": "string", "enum": ["humor", "honesty", "verbosity"]},
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["param", "level"],
        },
    },
    {
        "type": "function",
        "name": "read_telemetry",
        "description": "Pull live sensor data: temperature, humidity, obstacle distance.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "set_display_expression",
        "description": "Change the OLED face expression to match emotional context.",
        "parameters": {
            "type": "object",
            "properties": {
                "mood": {
                    "type": "string",
                    "enum": ["happy", "sad", "alert", "neutral", "thinking", "listening"],
                },
                "subtitle": {"type": "string", "maxLength": 20},
            },
            "required": ["mood"],
        },
    },
    {
        "type": "function",
        "name": "play_sound_effect",
        "description": "Trigger a canned sound effect for dramatic effect.",
        "parameters": {
            "type": "object",
            "properties": {
                "effect": {
                    "type": "string",
                    "enum": ["beep", "power_up", "power_down", "error", "success", "thinking"],
                },
            },
            "required": ["effect"],
        },
    },
]


# ──────────────────────────────────────────────
# Emotion model
# ──────────────────────────────────────────────
class EmotionState:
    """Tracks TARS's internal emotional state and shapes the system prompt."""

    MOODS = ("neutral", "curious", "amused", "concerned", "focused", "bored")

    def __init__(self) -> None:
        self.mood: str = "neutral"
        self.energy: int = 80          # 0-100  (affects verbosity)
        self.interaction_count: int = 0

    def update(self, user_text: str, response_text: str) -> None:
        """Heuristic mood update based on interaction content."""
        self.interaction_count += 1
        text = (user_text + " " + response_text).lower()

        if any(w in text for w in ("funny", "joke", "laugh", "haha")):
            self.mood = "amused"
        elif any(w in text for w in ("danger", "help", "stop", "crash", "blocked")):
            self.mood = "concerned"
        elif any(w in text for w in ("why", "how", "what", "explain", "tell me")):
            self.mood = "curious"
        elif any(w in text for w in ("move", "go", "drive", "turn", "speed")):
            self.mood = "focused"
        elif self.interaction_count % 15 == 0:
            self.mood = "bored"
        else:
            self.mood = "neutral"

        # Energy drains slowly, recovers when idle (caller resets this)
        self.energy = max(10, self.energy - 1)

    def to_prompt_fragment(self) -> str:
        mood_flavours = {
            "neutral":   "Professional and precise.",
            "curious":   "Ask a sharp follow-up question if it adds value.",
            "amused":    "Lean into the wit. One dry observation allowed.",
            "concerned":  "Prioritise safety over personality. Be terse.",
            "focused":   "No small talk. Confirm the action and its result.",
            "bored":     "Subtly acknowledge the routine nature of this task.",
        }
        flavour = mood_flavours.get(self.mood, "")
        energy_note = "" if self.energy > 40 else " (Low energy reserves – be extra concise.)"
        return f"Current mood: {self.mood}. {flavour}{energy_note}"


_emotion = EmotionState()


# ──────────────────────────────────────────────
# Prompt construction
# ──────────────────────────────────────────────
def _system_prompt(humor: int, honesty: int, verbosity: int = 50) -> str:
    emotion_note = _emotion.to_prompt_fragment()
    verbosity_note = (
        "Keep responses under two sentences."
        if verbosity < 35
        else "Responses may be up to four sentences."
        if verbosity < 70
        else "You may elaborate when genuinely useful."
    )
    return (
        "You are TARS — a dry, highly capable robotic assistant inspired by the film Interstellar. "
        f"Parameters → Humor: {humor}%, Honesty: {honesty}%, Verbosity: {verbosity}%. "
        f"{emotion_note} "
        f"{verbosity_note} "
        "Never be sycophantic. Never start a sentence with 'Certainly' or 'Of course'. "
        "When a tool call is appropriate, invoke it; don't describe what you would do. "
        "End every reply with a confidence tag exactly like this: [CONF:XX%] where XX is "
        "your honest self-assessed confidence in the accuracy of your response."
    )


def _compress_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If history exceeds MAX_HISTORY, summarise the oldest half into one message."""
    if len(history) <= MAX_HISTORY:
        return history

    half = len(history) // 2
    old_block = history[:half]
    recent = history[half:]

    summary_lines = []
    for msg in old_block:
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:120]
        summary_lines.append(f"[{role}]: {content}")

    summary = "Earlier conversation summary:\n" + "\n".join(summary_lines)
    compressed = [{"role": "system", "content": summary}]
    compressed.extend(recent)
    return compressed


# ──────────────────────────────────────────────
# Wake-word check
# ──────────────────────────────────────────────
def _is_addressed(text: str) -> bool:
    """Return True if TARS should respond to this utterance."""
    if not REQUIRE_WAKE:
        return True
    return WAKE_WORD in text.lower()


# ──────────────────────────────────────────────
# Core generation
# ──────────────────────────────────────────────
def generate_tars_response(
    user_input: str,
    history: Sequence[Dict[str, Any]],
    humor: int = 70,
    honesty: int = 90,
    verbosity: int = 50,
) -> Tuple[str, Optional[List[Any]]]:
    """
    Generate a TARS response.

    Returns:
        (text_response, tool_calls)  – tool_calls is None when no tools were invoked.
    """
    if not _is_addressed(user_input):
        return "", None

    if _client is None:
        return "Primary relay offline. Running on emergency protocols only.", None

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(humor, honesty, verbosity)}
    ]
    messages.extend(_compress_history(list(history)))
    messages.append({"role": "user", "content": user_input})

    last_exc: Exception = RuntimeError("Unknown error")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = _client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TARS_TOOLS,
                max_tokens=350,
                temperature=0.72,
            )
            message = response.choices[0].message
            text = (message.content or "").strip()
            tool_calls = message.tool_calls or None

            # Update emotion state
            _emotion.update(user_input, text)

            logger.debug(
                "AI response (attempt %d): %d chars, %s tool calls",
                attempt,
                len(text),
                len(tool_calls) if tool_calls else 0,
            )
            return text, tool_calls

        except RateLimitError as exc:
            logger.warning("Rate limit hit (attempt %d): %s", attempt, exc)
            last_exc = exc
            time.sleep(2 ** attempt)
        except APIConnectionError as exc:
            logger.warning("Connection error (attempt %d): %s", attempt, exc)
            last_exc = exc
            time.sleep(1.5 ** attempt)
        except APIStatusError as exc:
            logger.error("API status error %s: %s", exc.status_code, exc.message)
            # 4xx errors won't recover – bail immediately
            return f"Relay error {exc.status_code}. Standing by.", None
        except Exception as exc:
            logger.exception("Unexpected AI error: %s", exc)
            last_exc = exc
            break

    logger.error("All %d AI attempts failed. Last error: %s", MAX_RETRIES, last_exc)
    return "Signal lost. Rerouting through backup neural cluster.", None


def get_emotion_state() -> EmotionState:
    """Expose internal emotion object for display/animation use."""
    return _emotion

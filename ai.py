from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

load_dotenv()

_client: Optional[OpenAI] = None
try:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        _client = OpenAI(api_key=api_key)
    else:
        logger.warning("OPENAI_API_KEY is missing.")
except Exception as exc:
    logger.critical("Failed to initialize OpenAI client: %s", exc)
    _client = None

# Use gpt-4o-mini (fast + cheap) or gpt-3.5-turbo
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_HISTORY = int(os.getenv("TARS_MODEL_HISTORY_WINDOW", "10"))


TARS_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "control_motion",
        "description": "Drive the robot chassis in a given direction.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["forward", "backward", "left", "right", "stop"]},
                "speed": {"type": "integer", "minimum": 0, "maximum": 100, "default": 50},
            },
            "required": ["direction"],
        },
    },
    {
        "type": "function",
        "name": "adjust_parameters",
        "description": "Update personality parameters such as humor or honesty.",
        "parameters": {
            "type": "object",
            "properties": {
                "param": {"type": "string", "enum": ["humor", "honesty"]},
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["param", "level"],
        },
    },
    {
        "type": "function",
        "name": "read_telemetry",
        "description": "Read local sensor telemetry from the robot.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def _system_prompt(humor: int, honesty: int) -> str:
    return (
        "You are TARS, a dry, highly capable robotic assistant inspired by Interstellar. "
        f"Current parameters: Humor={humor}%, Honesty={honesty}%. "
        "Be concise, competent, and slightly witty. "
        "Never sound generic or overly enthusiastic. "
        "When a tool is appropriate, request it directly."
    )


def _recent_history(history: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list(history[-MAX_HISTORY:]) if history else []


def generate_tars_response(
    user_input: str, 
    history: Sequence[Dict[str, Any]], 
    humor: int = 70, 
    honesty: int = 90
) -> Tuple[str, Optional[List[Any]]]:
    """Generate TARS response and return any tool calls."""
    if _client is None:
        return "OpenAI client is not available.", None

    messages: List[Dict[str, Any]] = [{"role": "system", "content": _system_prompt(humor, honesty)}]
    messages.extend(_recent_history(history))
    messages.append({"role": "user", "content": user_input})

    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TARS_TOOLS,
            max_tokens=300,
            temperature=0.7
        )
        
        message = response.choices[0].message
        text = message.content or ""
        tool_calls = message.tool_calls  # This is the correct way

        return text.strip(), tool_calls

    except Exception as exc:
        logger.error("OpenAI request failed: %s", exc)
        return "Relay processing drop out.", None
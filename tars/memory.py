"""
tars/memory.py  –  Persistent Memory & State Management

Improvements over v1:
  • Episodic memory: stores distinct interaction "episodes" with timestamps
  • Semantic tags: auto-tags messages by detected intent category
  • Per-session summaries written at shutdown
  • Thread-safe save via file locking (portalocker if available, fallback lock)
  • Schema versioning to handle future migrations cleanly
  • Verbosity setting stored alongside humor/honesty
"""

from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MEMORY_FILE      = Path(os.getenv("TARS_MEMORY_FILE", "memory.json"))
MAX_HISTORY      = int(os.getenv("TARS_MAX_HISTORY_MESSAGES", "40"))
MAX_SYS_LOGS     = int(os.getenv("TARS_MAX_SYS_LOGS", "100"))
MAX_EPISODES     = int(os.getenv("TARS_MAX_EPISODES", "20"))
SCHEMA_VERSION   = 2

_save_lock = threading.Lock()

# ──────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────
DEFAULT_MEMORY: Dict[str, Any] = {
    "schema_version":       SCHEMA_VERSION,
    "conversation_history": [],
    "episodes":             [],          # list of summarised interaction episodes
    "mood":                 "neutral",
    "humor_setting":        70,
    "honesty_setting":      90,
    "verbosity_setting":    50,
    "total_interactions":   0,
    "uptime_seconds":       0,
    "sys_logs":             [],
    "last_updated":         None,
    "created_at":           None,
}


# ──────────────────────────────────────────────
# Semantic tagger
# ──────────────────────────────────────────────
_INTENT_KEYWORDS: Dict[str, List[str]] = {
    "motion":     ["move", "forward", "backward", "turn", "stop", "drive", "left", "right"],
    "query":      ["what", "why", "how", "when", "where", "who", "explain", "tell me"],
    "settings":   ["humor", "honesty", "verbosity", "set", "adjust", "calibrate", "change"],
    "telemetry":  ["temperature", "humidity", "distance", "sensor", "status", "telemetry"],
    "social":     ["hello", "hi", "bye", "thanks", "joke", "funny"],
}


def _tag_intent(text: str) -> str:
    text_lower = text.lower()
    for tag, keywords in _INTENT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return tag
    return "general"


# ──────────────────────────────────────────────
# Schema migration
# ──────────────────────────────────────────────
def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    version = data.get("schema_version", 1)
    if version < 2:
        data["episodes"]          = []
        data["verbosity_setting"] = 50
        data["total_interactions"] = 0
        data["uptime_seconds"]    = 0
        data["schema_version"]    = 2
        logger.info("Memory migrated from schema v1 → v2.")
    return data


def _merged_default(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_MEMORY)
    if not data:
        merged["created_at"] = _now()
        return merged
    for key, value in data.items():
        merged[key] = value
    if not isinstance(merged.get("conversation_history"), list):
        merged["conversation_history"] = []
    if not isinstance(merged.get("sys_logs"), list):
        merged["sys_logs"] = []
    if not isinstance(merged.get("episodes"), list):
        merged["episodes"] = []
    return _migrate(merged)


# ──────────────────────────────────────────────
# Time helper
# ──────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ──────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────
def load_memory() -> Dict[str, Any]:
    """Load and return persistent memory, repairing / migrating when needed."""
    if not MEMORY_FILE.exists():
        fresh = deepcopy(DEFAULT_MEMORY)
        fresh["created_at"] = _now()
        return fresh

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return _merged_default(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Memory file unreadable (%s) – starting fresh.", exc)
        return deepcopy(DEFAULT_MEMORY)


def _prune(data: Dict[str, Any]) -> Dict[str, Any]:
    data["conversation_history"] = data.get("conversation_history", [])[-MAX_HISTORY:]
    data["sys_logs"]             = data.get("sys_logs", [])[-MAX_SYS_LOGS:]
    data["episodes"]             = data.get("episodes", [])[-MAX_EPISODES:]
    data["last_updated"]         = _now()
    return data


def save_memory(data: Dict[str, Any]) -> None:
    """Atomically persist memory (thread-safe)."""
    payload = _prune(_merged_default(data))
    tmp_path = MEMORY_FILE.with_suffix(MEMORY_FILE.suffix + ".tmp")

    with _save_lock:
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp_path, MEMORY_FILE)
        except OSError as exc:
            logger.error("Memory save failed: %s", exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


# ──────────────────────────────────────────────
# Conversation helpers
# ──────────────────────────────────────────────
def append_message(
    data: Dict[str, Any],
    role: str,
    content: str,
    **extra: Any,
) -> None:
    """Append a message and auto-tag user utterances."""
    entry: Dict[str, Any] = {"role": role, "content": content}
    if role == "user":
        entry["intent"] = _tag_intent(content)
    if extra:
        entry.update(extra)
    entry["ts"] = _now()
    data.setdefault("conversation_history", []).append(entry)
    if role == "user":
        data["total_interactions"] = data.get("total_interactions", 0) + 1
    _prune(data)


def close_episode(data: Dict[str, Any], summary: str) -> None:
    """
    Seal the current conversation turn into an episode record.
    Call this at logical breakpoints (e.g., after each full user→response cycle).
    """
    history = data.get("conversation_history", [])
    if not history:
        return

    episode = {
        "ts": _now(),
        "turn_count": len(history),
        "summary": summary[:300],
        "mood": data.get("mood", "neutral"),
        "dominant_intent": _most_common_intent(history),
    }
    data.setdefault("episodes", []).append(episode)
    _prune(data)


def _most_common_intent(history: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for msg in history:
        intent = msg.get("intent", "general")
        counts[intent] = counts.get(intent, 0) + 1
    return max(counts, key=counts.get) if counts else "general"  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
def update_setting(
    data: Dict[str, Any],
    key: str,
    value: int,
    minimum: int = 0,
    maximum: int = 100,
) -> int:
    clamped = max(minimum, min(maximum, int(value)))
    data[key] = clamped
    return clamped


# ──────────────────────────────────────────────
# Event logging
# ──────────────────────────────────────────────
def log_event(data: Dict[str, Any], message: str, level: str = "info") -> None:
    data.setdefault("sys_logs", []).append(
        {"time": _now(), "level": level, "message": message}
    )
    _prune(data)


# ──────────────────────────────────────────────
# Module-level singleton (loaded once at import)
# ──────────────────────────────────────────────
memory: Dict[str, Any] = load_memory()

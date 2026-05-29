from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MEMORY_FILE = Path(os.getenv("TARS_MEMORY_FILE", "memory.json"))
MAX_HISTORY_MESSAGES = int(os.getenv("TARS_MAX_HISTORY_MESSAGES", "40"))
MAX_SYS_LOGS = int(os.getenv("TARS_MAX_SYS_LOGS", "100"))


DEFAULT_MEMORY: Dict[str, Any] = {
    "conversation_history": [],
    "mood": "neutral",
    "humor_setting": 70,
    "honesty_setting": 90,
    "sys_logs": [],
    "last_updated": None,
}


def _merged_default(data: Dict[str, Any] | None) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_MEMORY)
    if not data:
        return merged

    for key, value in data.items():
        merged[key] = value

    if not isinstance(merged.get("conversation_history"), list):
        merged["conversation_history"] = []
    if not isinstance(merged.get("sys_logs"), list):
        merged["sys_logs"] = []

    return merged


def load_memory() -> Dict[str, Any]:
    """Load persistent memory, repairing missing keys when possible."""
    if not MEMORY_FILE.exists():
        return deepcopy(DEFAULT_MEMORY)

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return _merged_default(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Memory file could not be read: %s", exc)
        return deepcopy(DEFAULT_MEMORY)


def _prune_memory(data: Dict[str, Any]) -> Dict[str, Any]:
    data["conversation_history"] = data.get("conversation_history", [])[-MAX_HISTORY_MESSAGES:]
    data["sys_logs"] = data.get("sys_logs", [])[-MAX_SYS_LOGS:]
    data["last_updated"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return data


def save_memory(data: Dict[str, Any]) -> None:
    """Persist memory atomically to reduce corruption risk."""
    payload = _prune_memory(_merged_default(data))
    tmp_path = MEMORY_FILE.with_suffix(MEMORY_FILE.suffix + ".tmp")

    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_path, MEMORY_FILE)
    except OSError as exc:
        logger.error("Failed to save memory: %s", exc)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def append_message(data: Dict[str, Any], role: str, content: str, **extra: Any) -> None:
    entry = {"role": role, "content": content}
    if extra:
        entry.update(extra)
    data.setdefault("conversation_history", []).append(entry)
    _prune_memory(data)


def log_event(data: Dict[str, Any], message: str, level: str = "info") -> None:
    data.setdefault("sys_logs", []).append(
        {
            "time": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "level": level,
            "message": message,
        }
    )
    _prune_memory(data)


def update_setting(data: Dict[str, Any], key: str, value: int, minimum: int = 0, maximum: int = 100) -> int:
    value = max(minimum, min(maximum, int(value)))
    data[key] = value
    return value


memory = load_memory()

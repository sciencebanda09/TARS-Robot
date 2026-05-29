"""Unit tests for tars/memory.py – runs without hardware."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import tempfile
from pathlib import Path

import pytest
import tars.memory as mem


@pytest.fixture(autouse=True)
def temp_memory_file(tmp_path, monkeypatch):
    p = tmp_path / "test_memory.json"
    monkeypatch.setattr(mem, "MEMORY_FILE", p)
    yield p


def test_default_memory_keys():
    data = mem.load_memory()
    for key in ("conversation_history", "humor_setting", "honesty_setting", "episodes"):
        assert key in data


def test_append_message_tags_intent():
    data = mem.load_memory()
    mem.append_message(data, "user", "move forward please")
    last = data["conversation_history"][-1]
    assert last["intent"] == "motion"


def test_append_message_increments_interactions():
    data = mem.load_memory()
    before = data.get("total_interactions", 0)
    mem.append_message(data, "user", "hello")
    assert data["total_interactions"] == before + 1


def test_update_setting_clamps():
    data = mem.load_memory()
    assert mem.update_setting(data, "humor_setting", 150) == 100
    assert mem.update_setting(data, "humor_setting", -10) == 0


def test_save_and_reload(tmp_path, monkeypatch):
    p = tmp_path / "mem.json"
    monkeypatch.setattr(mem, "MEMORY_FILE", p)
    data = mem.load_memory()
    data["humor_setting"] = 42
    mem.save_memory(data)
    reloaded = mem.load_memory()
    assert reloaded["humor_setting"] == 42


def test_close_episode():
    data = mem.load_memory()
    mem.append_message(data, "user", "test command")
    mem.append_message(data, "assistant", "acknowledged")
    mem.close_episode(data, summary="Test exchange")
    assert len(data["episodes"]) == 1
    assert "Test exchange" in data["episodes"][0]["summary"]


def test_schema_migration():
    old_data = {
        "schema_version": 1,
        "conversation_history": [],
        "humor_setting": 70,
        "honesty_setting": 90,
        "sys_logs": [],
    }
    migrated = mem._merged_default(old_data)
    assert migrated["schema_version"] == 2
    assert "verbosity_setting" in migrated
    assert "episodes" in migrated

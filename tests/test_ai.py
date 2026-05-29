"""Unit tests for tars/ai.py – mocks the OpenAI client."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
import tars.ai as ai_mod


def _make_mock_response(text: str, tool_calls=None):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_generate_returns_text():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response("Hello. [CONF:90%]")
    ai_mod._client = mock_client

    text, calls = ai_mod.generate_tars_response("Hi TARS", history=[])
    assert "Hello" in text
    assert calls is None


def test_generate_returns_tool_calls():
    mock_tool = MagicMock()
    mock_tool.function.name = "control_motion"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response("", [mock_tool])
    ai_mod._client = mock_client

    text, calls = ai_mod.generate_tars_response("Move forward", history=[])
    assert calls is not None
    assert len(calls) == 1


def test_no_client_returns_offline_message():
    ai_mod._client = None
    text, calls = ai_mod.generate_tars_response("test", history=[])
    assert "offline" in text.lower() or "relay" in text.lower() or "available" in text.lower()
    assert calls is None


def test_wake_word_filtering(monkeypatch):
    monkeypatch.setenv("TARS_REQUIRE_WAKE_WORD", "true")
    monkeypatch.setattr(ai_mod, "REQUIRE_WAKE", True)
    monkeypatch.setattr(ai_mod, "WAKE_WORD", "tars")

    text, calls = ai_mod.generate_tars_response("the weather is nice", history=[])
    assert text == ""
    assert calls is None


def test_emotion_state_updates():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response("Moving now. [CONF:95%]")
    ai_mod._client = mock_client

    ai_mod.generate_tars_response("drive forward", history=[])
    emotion = ai_mod.get_emotion_state()
    assert emotion.mood in ai_mod.EmotionState.MOODS

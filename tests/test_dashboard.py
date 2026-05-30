"""Tests for tars/dashboard.py – spins up a real HTTPServer on a random port."""

import sys, os, json, queue, time, threading, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tars.dashboard as dash


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start(port: int):
    import tars.dashboard as d
    d.DASHBOARD_PORT = port
    mem = {"conversation_history": [], "sys_logs": [], "episodes": [],
           "humor_setting": 70, "honesty_setting": 90, "verbosity_setting": 50,
           "mood": "neutral", "total_interactions": 5, "uptime_seconds": 120}
    cmd_q: queue.Queue = queue.Queue()
    d.configure(
        memory=mem,
        command_queue=cmd_q,
        speak_fn=lambda t: None,
        motion_fn=lambda dr, sp: None,
    )
    d.update_state({"temp_c": 28.5, "distance_cm": 80.0, "fan_on": False})
    d.start_dashboard()
    time.sleep(0.3)   # let server bind
    return d, cmd_q


def test_dashboard_root_returns_html():
    port = _free_port()
    d, _ = _start(port)
    try:
        r = urllib.request.urlopen(f"http://localhost:{port}/", timeout=3)
        body = r.read().decode()
        assert "T.A.R.S" in body
        assert r.status == 200
    finally:
        d.stop_dashboard()


def test_api_state_returns_json():
    port = _free_port()
    d, _ = _start(port)
    try:
        r = urllib.request.urlopen(f"http://localhost:{port}/api/state", timeout=3)
        data = json.loads(r.read())
        assert "humor" in data
        assert "temp_c" in data
    finally:
        d.stop_dashboard()


def test_api_command_queues_command():
    port = _free_port()
    d, cmd_q = _start(port)
    try:
        payload = json.dumps({"command": "move forward"}).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/api/command",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        r = urllib.request.urlopen(req, timeout=3)
        result = json.loads(r.read())
        assert result.get("queued") == "move forward"
        assert cmd_q.get_nowait() == "move forward"
    finally:
        d.stop_dashboard()


def test_api_history_returns_list():
    port = _free_port()
    d, _ = _start(port)
    try:
        r = urllib.request.urlopen(f"http://localhost:{port}/api/history", timeout=3)
        data = json.loads(r.read())
        assert isinstance(data, list)
    finally:
        d.stop_dashboard()


def test_api_404():
    port = _free_port()
    d, _ = _start(port)
    try:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/nonexistent", timeout=3)
            assert False, "Should have raised HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        d.stop_dashboard()

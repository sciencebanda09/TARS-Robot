"""
tars/dashboard.py  –  Live Web Dashboard  (NEW)

Serves a local HTTP dashboard over WiFi so you can monitor and
control TARS from any browser on the same network.

Endpoints:
  GET  /              → full dashboard HTML (single-file, no CDN needed)
  GET  /api/state     → JSON snapshot of all live data
  POST /api/command   → send a voice-style command to TARS
  POST /api/speak     → force TARS to say something
  POST /api/motion    → direct motor control
  GET  /api/history   → last N conversation messages
  GET  /api/logs      → last N system log entries
  GET  /api/episodes  → episodic memory list

Runs in its own daemon thread so it never blocks the main loop.
Uses only the stdlib http.server – zero extra dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

DASHBOARD_PORT = int(os.getenv("TARS_DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("TARS_DASHBOARD_HOST", "0.0.0.0")

# ── Shared state (populated by main.py) ──────────────────
_state: Dict[str, Any] = {}
_memory_ref: Optional[Dict[str, Any]] = None
_command_queue: Optional[Any] = None   # queue.Queue injected from main

# Callbacks injected from main.py
_speak_fn:  Optional[Callable[[str], None]] = None
_motion_fn: Optional[Callable[[str, int], None]] = None


def configure(
    memory: Dict[str, Any],
    command_queue: Any,
    speak_fn: Callable[[str], None],
    motion_fn: Callable[[str, int], None],
) -> None:
    """Call from main.py before starting the dashboard."""
    global _memory_ref, _command_queue, _speak_fn, _motion_fn
    _memory_ref    = memory
    _command_queue = command_queue
    _speak_fn      = speak_fn
    _motion_fn     = motion_fn


def update_state(patch: Dict[str, Any]) -> None:
    """main.py calls this each loop to push live data."""
    _state.update(patch)
    _state["ts"] = time.time()


# ── Request handler ───────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default log
        logger.debug("HTTP %s", fmt % args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/":
            self._send_html(_DASHBOARD_HTML)
            return

        if path == "/api/state":
            self._send_json(_build_state())
            return

        if path == "/api/history":
            msgs = (_memory_ref or {}).get("conversation_history", [])
            self._send_json(msgs[-30:])
            return

        if path == "/api/logs":
            logs = (_memory_ref or {}).get("sys_logs", [])
            self._send_json(logs[-50:])
            return

        if path == "/api/episodes":
            eps = (_memory_ref or {}).get("episodes", [])
            self._send_json(eps)
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        body = self._read_body()

        if path == "/api/command":
            cmd = str(body.get("command", "")).strip()
            if cmd and _command_queue is not None:
                _command_queue.put(cmd)
                self._send_json({"queued": cmd})
            else:
                self._send_json({"error": "empty command or not configured"}, 400)
            return

        if path == "/api/speak":
            text = str(body.get("text", "")).strip()
            if text and _speak_fn:
                _speak_fn(text)
                self._send_json({"spoken": text})
            else:
                self._send_json({"error": "empty text"}, 400)
            return

        if path == "/api/motion":
            direction = str(body.get("direction", "stop"))
            speed     = int(body.get("speed", 50))
            if _motion_fn:
                _motion_fn(direction, speed)
                self._send_json({"direction": direction, "speed": speed})
            else:
                self._send_json({"error": "motion not configured"}, 400)
            return

        self._send_json({"error": "not found"}, 404)


def _build_state() -> Dict[str, Any]:
    mem = _memory_ref or {}
    return {
        **_state,
        "humor":     mem.get("humor_setting", 70),
        "honesty":   mem.get("honesty_setting", 90),
        "verbosity": mem.get("verbosity_setting", 50),
        "mood":      mem.get("mood", "neutral"),
        "interactions": mem.get("total_interactions", 0),
        "uptime_s":  mem.get("uptime_seconds", 0),
    }


# ── Server lifecycle ──────────────────────────────────────
_server: Optional[HTTPServer] = None


def start_dashboard() -> None:
    global _server
    try:
        _server = HTTPServer((DASHBOARD_HOST, DASHBOARD_PORT), _Handler)
        t = threading.Thread(
            target=_server.serve_forever, daemon=True, name="tars-dashboard"
        )
        t.start()
        logger.info(
            "Dashboard live at http://%s:%d",
            "localhost" if DASHBOARD_HOST == "0.0.0.0" else DASHBOARD_HOST,
            DASHBOARD_PORT,
        )
    except Exception as exc:
        logger.error("Dashboard failed to start: %s", exc)


def stop_dashboard() -> None:
    if _server:
        _server.shutdown()


# ── Embedded dashboard HTML ───────────────────────────────
# Single-file: HTML + CSS + JS, no external dependencies
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T.A.R.S Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap');

  :root {
    --bg:        #050a0e;
    --bg2:       #0a1520;
    --bg3:       #0f1e2e;
    --accent:    #00d4ff;
    --accent2:   #ff6b35;
    --green:     #39ff14;
    --red:       #ff3333;
    --yellow:    #ffd700;
    --text:      #b8d4e8;
    --text-dim:  #4a7090;
    --border:    #1a3a55;
    --glow:      0 0 12px rgba(0,212,255,0.4);
  }

  * { margin:0; padding:0; box-sizing:border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Scanline overlay */
  body::before {
    content:'';
    position:fixed; inset:0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.08) 2px,
      rgba(0,0,0,0.08) 4px
    );
    pointer-events:none;
    z-index:999;
  }

  header {
    background: linear-gradient(135deg, var(--bg2) 0%, var(--bg3) 100%);
    border-bottom: 1px solid var(--accent);
    padding: 18px 32px;
    display: flex;
    align-items: center;
    gap: 20px;
    box-shadow: var(--glow);
    position: sticky; top:0; z-index:100;
  }

  .logo {
    font-family: 'Orbitron', monospace;
    font-weight: 900;
    font-size: 1.6rem;
    color: var(--accent);
    letter-spacing: 0.3em;
    text-shadow: var(--glow);
  }

  .logo span { color: var(--accent2); }

  .status-pill {
    margin-left: auto;
    display: flex; align-items: center; gap: 8px;
    font-size: 0.75rem; color: var(--text-dim);
  }

  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 1.5s ease-in-out infinite;
  }

  .dot.offline { background: var(--red); box-shadow: 0 0 8px var(--red); animation:none; }

  @keyframes pulse {
    0%,100% { opacity:1; } 50% { opacity:0.4; }
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 20px;
    padding: 24px 32px;
    max-width: 1400px;
    margin: 0 auto;
  }

  .card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 20px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s;
  }

  .card:hover { border-color: var(--accent); }

  .card::before {
    content:'';
    position:absolute; top:0; left:0; right:0; height:2px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity:0;
    transition: opacity 0.3s;
  }
  .card:hover::before { opacity:1; }

  .card-title {
    font-family: 'Orbitron', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.2em;
    color: var(--text-dim);
    margin-bottom: 16px;
    text-transform: uppercase;
  }

  /* Metric display */
  .metric { display:flex; align-items:baseline; gap:6px; margin-bottom:12px; }
  .metric-val {
    font-family: 'Orbitron', monospace;
    font-size: 2.2rem;
    font-weight: 700;
    color: var(--accent);
    text-shadow: var(--glow);
    line-height:1;
  }
  .metric-unit { font-size:0.8rem; color:var(--text-dim); }
  .metric-label { font-size:0.7rem; color:var(--text-dim); margin-bottom:4px; }

  /* Stat bar */
  .stat-row { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
  .stat-name { font-size:0.72rem; color:var(--text-dim); min-width:80px; }
  .stat-bar-wrap { flex:1; height:4px; background:var(--bg3); border-radius:2px; overflow:hidden; }
  .stat-bar { height:100%; background:var(--accent); border-radius:2px; transition:width 0.5s ease; }
  .stat-val { font-size:0.72rem; color:var(--accent); min-width:30px; text-align:right; }

  /* OLED face preview */
  .face-preview {
    width:128px; height:64px;
    background:#000;
    border:1px solid var(--accent);
    border-radius:2px;
    margin: 0 auto 16px;
    display:flex; align-items:center; justify-content:center;
    position:relative;
    box-shadow: var(--glow);
  }
  .face-preview canvas { display:block; }

  .mood-label {
    text-align:center;
    font-family:'Orbitron',monospace;
    font-size:0.6rem;
    letter-spacing:0.15em;
    color:var(--accent);
  }

  /* Motion pad */
  .dpad {
    display:grid;
    grid-template-areas: ". up ." "left stop right" ". down .";
    grid-template-columns: 1fr 1fr 1fr;
    gap:8px;
    max-width:200px;
    margin:0 auto;
  }

  .dpad-btn {
    background: var(--bg3);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    font-size:0.8rem;
    padding: 14px 10px;
    cursor:pointer;
    border-radius:3px;
    transition: all 0.15s;
    user-select:none;
  }
  .dpad-btn:hover  { background:var(--accent); color:var(--bg); border-color:var(--accent); }
  .dpad-btn:active { transform:scale(0.93); }
  .dpad-btn.stop-btn { background:#1a0f0f; border-color:var(--red); color:var(--red); }
  .dpad-btn.stop-btn:hover { background:var(--red); color:#fff; }

  [data-dir="forward"] { grid-area:up; }
  [data-dir="left"]    { grid-area:left; }
  [data-dir="stop"]    { grid-area:stop; }
  [data-dir="right"]   { grid-area:right; }
  [data-dir="backward"]{ grid-area:down; }

  /* Speed slider */
  .slider-wrap { margin-top:14px; display:flex; align-items:center; gap:10px; font-size:0.72rem; }
  input[type=range] {
    flex:1; accent-color:var(--accent);
    height:4px; cursor:pointer;
  }

  /* Console / log */
  .console {
    background:#000;
    border:1px solid var(--border);
    border-radius:3px;
    padding:12px;
    height:200px;
    overflow-y:auto;
    font-size:0.72rem;
    line-height:1.7;
  }
  .console::-webkit-scrollbar { width:4px; }
  .console::-webkit-scrollbar-track { background:var(--bg); }
  .console::-webkit-scrollbar-thumb { background:var(--accent); }

  .log-line { margin-bottom:2px; }
  .log-line .ts    { color:var(--text-dim); margin-right:8px; }
  .log-line .role  { margin-right:8px; }
  .role-user       { color:var(--yellow); }
  .role-assistant  { color:var(--accent); }
  .role-tool       { color:var(--green); }
  .role-system     { color:var(--red); }

  /* Command input */
  .cmd-wrap { display:flex; gap:8px; margin-top:14px; }
  .cmd-input {
    flex:1;
    background:var(--bg3);
    border:1px solid var(--border);
    border-radius:3px;
    color:var(--text);
    font-family:'Share Tech Mono',monospace;
    font-size:0.8rem;
    padding:10px 14px;
    outline:none;
    transition: border-color 0.2s;
  }
  .cmd-input:focus { border-color:var(--accent); box-shadow:var(--glow); }
  .cmd-input::placeholder { color:var(--text-dim); }

  .btn {
    background:var(--bg3);
    border:1px solid var(--accent);
    color:var(--accent);
    font-family:'Share Tech Mono',monospace;
    font-size:0.75rem;
    padding:10px 18px;
    border-radius:3px;
    cursor:pointer;
    transition: all 0.15s;
    white-space:nowrap;
  }
  .btn:hover  { background:var(--accent); color:var(--bg); }
  .btn:active { transform:scale(0.96); }
  .btn.danger { border-color:var(--red); color:var(--red); }
  .btn.danger:hover { background:var(--red); color:#fff; }

  /* Trend arrows */
  .trend { font-size:0.75rem; margin-left:6px; }
  .trend.up   { color:var(--red); }
  .trend.down { color:var(--accent); }
  .trend.stable { color:var(--text-dim); }

  /* Responsive */
  @media(max-width:600px) {
    .grid { padding:12px; gap:12px; }
    header { padding:14px 16px; }
    .logo { font-size:1.1rem; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">T<span>.</span>A<span>.</span>R<span>.</span>S</div>
  <div style="font-size:0.7rem;color:var(--text-dim);letter-spacing:0.1em;">MISSION CONTROL</div>
  <div class="status-pill">
    <div class="dot" id="conn-dot"></div>
    <span id="conn-label">CONNECTING</span>
  </div>
</header>

<div class="grid">

  <!-- Face / Mood -->
  <div class="card" style="grid-column:span 1;">
    <div class="card-title">◈ Expression Matrix</div>
    <div class="face-preview">
      <canvas id="face-canvas" width="128" height="64"></canvas>
    </div>
    <div class="mood-label" id="mood-label">NEUTRAL</div>
  </div>

  <!-- Telemetry -->
  <div class="card">
    <div class="card-title">◈ Sensor Telemetry</div>
    <div class="metric">
      <span class="metric-val" id="temp-val">--</span>
      <span class="metric-unit">°C</span>
      <span class="trend" id="temp-trend">→</span>
    </div>
    <div class="metric-label">TEMPERATURE</div>

    <div class="metric" style="margin-top:16px;">
      <span class="metric-val" id="dist-val">--</span>
      <span class="metric-unit">cm</span>
      <span class="trend" id="dist-trend">→</span>
    </div>
    <div class="metric-label">OBSTACLE DISTANCE</div>

    <div class="metric" style="margin-top:16px;">
      <span class="metric-val" id="humid-val">--</span>
      <span class="metric-unit">%</span>
    </div>
    <div class="metric-label">HUMIDITY</div>
  </div>

  <!-- Parameters -->
  <div class="card">
    <div class="card-title">◈ Personality Parameters</div>
    <div class="stat-row">
      <span class="stat-name">HUMOR</span>
      <div class="stat-bar-wrap"><div class="stat-bar" id="bar-humor" style="width:70%"></div></div>
      <span class="stat-val" id="val-humor">70%</span>
    </div>
    <div class="stat-row">
      <span class="stat-name">HONESTY</span>
      <div class="stat-bar-wrap"><div class="stat-bar" id="bar-honesty" style="width:90%"></div></div>
      <span class="stat-val" id="val-honesty">90%</span>
    </div>
    <div class="stat-row">
      <span class="stat-name">VERBOSITY</span>
      <div class="stat-bar-wrap"><div class="stat-bar" id="bar-verbose" style="width:50%"></div></div>
      <span class="stat-val" id="val-verbose">50%</span>
    </div>

    <div style="margin-top:20px;">
      <div class="stat-row">
        <span class="stat-name">INTERACTIONS</span>
        <span style="font-family:'Orbitron',monospace;font-size:1.2rem;color:var(--accent);" id="interactions">0</span>
      </div>
      <div class="stat-row">
        <span class="stat-name">UPTIME</span>
        <span style="font-size:0.8rem;color:var(--text);" id="uptime">--</span>
      </div>
      <div class="stat-row">
        <span class="stat-name">FAN</span>
        <span style="font-size:0.8rem;" id="fan-state">--</span>
      </div>
      <div class="stat-row">
        <span class="stat-name">AWAKE</span>
        <span style="font-size:0.8rem;" id="pir-state">--</span>
      </div>
    </div>
  </div>

  <!-- Motion Control -->
  <div class="card">
    <div class="card-title">◈ Motion Control</div>
    <div class="dpad">
      <button class="dpad-btn" data-dir="forward"  onclick="sendMotion('forward')">▲</button>
      <button class="dpad-btn" data-dir="left"     onclick="sendMotion('left')">◄</button>
      <button class="dpad-btn stop-btn" data-dir="stop" onclick="sendMotion('stop')">■</button>
      <button class="dpad-btn" data-dir="right"    onclick="sendMotion('right')">►</button>
      <button class="dpad-btn" data-dir="backward" onclick="sendMotion('backward')">▼</button>
    </div>
    <div class="slider-wrap">
      <span>SPD</span>
      <input type="range" id="speed-slider" min="20" max="100" value="50">
      <span id="speed-label">50</span>
    </div>
  </div>

  <!-- Command Console -->
  <div class="card" style="grid-column:span 2;">
    <div class="card-title">◈ Command Interface</div>
    <div class="console" id="console"></div>
    <div class="cmd-wrap" style="margin-top:10px;">
      <input class="cmd-input" id="cmd-input" placeholder="Issue command to TARS..." autocomplete="off">
      <button class="btn" onclick="sendCommand()">SEND</button>
      <button class="btn" onclick="sendSpeak()">SPEAK</button>
    </div>
  </div>

</div>

<script>
const API = '';  // same origin
let lastTs = 0;

// ── Fetch & render state ─────────────────────────────────
async function fetchState() {
  try {
    const r = await fetch(API + '/api/state');
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    renderState(d);
    setConn(true);
  } catch(e) {
    setConn(false);
  }
}

async function fetchHistory() {
  try {
    const r = await fetch(API + '/api/history');
    const msgs = await r.json();
    renderConsole(msgs);
  } catch(e) {}
}

function setConn(ok) {
  document.getElementById('conn-dot').className   = 'dot' + (ok ? '' : ' offline');
  document.getElementById('conn-label').textContent = ok ? 'ONLINE' : 'OFFLINE';
}

function renderState(d) {
  // Telemetry
  setText('temp-val',   d.temp_c   != null ? d.temp_c  : '--');
  setText('dist-val',   d.distance_cm != null ? Math.round(d.distance_cm) : '--');
  setText('humid-val',  d.humidity != null ? d.humidity : '--');
  renderTrend('temp-trend', d.temp_trend);
  renderTrend('dist-trend', d.dist_trend);

  // Parameters
  setBar('humor',   d.humor   ?? 70);
  setBar('honesty', d.honesty ?? 90);
  setBar('verbose', d.verbosity ?? 50);

  // Stats
  setText('interactions', d.interactions ?? 0);
  setText('uptime', formatUptime(d.uptime_s ?? 0));
  const fan = document.getElementById('fan-state');
  if (d.fan_on != null) {
    fan.textContent  = d.fan_on ? 'ON' : 'OFF';
    fan.style.color  = d.fan_on ? 'var(--red)' : 'var(--green)';
  }
  const pir = document.getElementById('pir-state');
  if (d.pir_awake != null) {
    pir.textContent = d.pir_awake ? 'YES' : 'SLEEPING';
    pir.style.color = d.pir_awake ? 'var(--green)' : 'var(--text-dim)';
  }

  // Mood / face
  const mood = (d.mood || 'neutral').toUpperCase();
  document.getElementById('mood-label').textContent = mood;
  drawFace(d.mood || 'neutral');
}

function renderTrend(id, trend) {
  const el = document.getElementById(id);
  if (!el || !trend) return;
  const map = { rising:'↑', approaching:'↓', falling:'↓', receding:'↑', stable:'→' };
  const cls = { rising:'up', approaching:'up', falling:'down', receding:'down', stable:'stable' };
  el.textContent  = map[trend] || '→';
  el.className    = 'trend ' + (cls[trend] || 'stable');
}

function setBar(key, val) {
  const bar = document.getElementById('bar-' + key);
  const lbl = document.getElementById('val-' + key);
  if (bar) bar.style.width = val + '%';
  if (lbl) lbl.textContent = val + '%';
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function formatUptime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return `${h}h ${m}m ${sec}s`;
}

// ── OLED face canvas renderer ─────────────────────────────
function drawFace(mood) {
  const canvas = document.getElementById('face-canvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 128, 64);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, 128, 64);
  ctx.strokeStyle = '#00d4ff';
  ctx.fillStyle   = '#00d4ff';
  ctx.lineWidth = 3;
  mood = (mood || 'neutral').toLowerCase();

  if (mood === 'happy' || mood === 'positive') {
    arc(ctx, 31, 31, 13, Math.PI, 2*Math.PI);
    arc(ctx, 97, 31, 13, Math.PI, 2*Math.PI);
  } else if (mood === 'sad' || mood === 'blocked') {
    line(ctx, 18, 30, 44, 22); line(ctx, 84, 22, 110, 30);
  } else if (mood === 'alert' || mood === 'warning') {
    rect(ctx, 18, 20, 26, 22); rect(ctx, 84, 20, 26, 22);
    ctx.fillRect(28, 28, 6, 6); ctx.fillRect(94, 28, 6, 6);
  } else if (mood === 'thinking') {
    arc(ctx, 31, 32, 12, 3.5, 5.9);
    arc(ctx, 97, 34, 11, 3.5, 5.9);
  } else if (mood === 'listening' || mood === 'curious') {
    circle(ctx, 31, 29, 13);
    circle(ctx, 97, 29, 13);
  } else {
    // neutral
    ctx.fillRect(18, 24, 26, 12);
    ctx.fillRect(84, 24, 26, 12);
  }
}

function arc(ctx, cx, cy, r, s, e) {
  ctx.beginPath(); ctx.arc(cx, cy, r, s, e); ctx.stroke();
}
function circle(ctx, cx, cy, r) {
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2*Math.PI); ctx.stroke();
}
function line(ctx, x1, y1, x2, y2) {
  ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
}
function rect(ctx, x, y, w, h) {
  ctx.beginPath(); ctx.strokeRect(x, y, w, h);
}

// ── Console ──────────────────────────────────────────────
function renderConsole(msgs) {
  const el = document.getElementById('console');
  if (!msgs.length) return;
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
  el.innerHTML = msgs.map(m => {
    const role = m.role || 'system';
    const ts   = (m.ts || '').substring(11,19);
    const text = (m.content || '').substring(0,200);
    return `<div class="log-line">
      <span class="ts">${ts}</span>
      <span class="role role-${role}">[${role.toUpperCase()}]</span>
      <span>${escHtml(text)}</span>
    </div>`;
  }).join('');
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Actions ───────────────────────────────────────────────
async function sendMotion(dir) {
  const speed = parseInt(document.getElementById('speed-slider').value);
  await fetch(API + '/api/motion', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({direction: dir, speed})
  });
}

async function sendCommand() {
  const inp = document.getElementById('cmd-input');
  const cmd = inp.value.trim();
  if (!cmd) return;
  await fetch(API + '/api/command', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({command: cmd})
  });
  inp.value = '';
  setTimeout(fetchHistory, 1500);
}

async function sendSpeak() {
  const inp = document.getElementById('cmd-input');
  const text = inp.value.trim();
  if (!text) return;
  await fetch(API + '/api/speak', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text})
  });
  inp.value = '';
}

document.getElementById('cmd-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendCommand();
});

document.getElementById('speed-slider').addEventListener('input', function() {
  document.getElementById('speed-label').textContent = this.value;
});

// ── Poll loop ─────────────────────────────────────────────
fetchState();
fetchHistory();
setInterval(fetchState,   2000);
setInterval(fetchHistory, 3000);
drawFace('neutral');
</script>
</body>
</html>
"""

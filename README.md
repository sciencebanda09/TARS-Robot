# T.A.R.S — Tactical Assistance & Response System

> *"TARS, what's your honesty parameter?"  
> "Ninety percent."  
> "Hmm."  
> "You can't handle ninety percent."*

A GPT-powered, voice-driven robot assistant for Raspberry Pi. Inspired by the robot from *Interstellar* — dry wit, precise execution, and just enough personality to be unsettling.

---

## What's New in v2

| Area | v1 | v2 |
|---|---|---|
| **AI** | Single call, basic retry | Retry with back-off, confidence tags, emotion model, wake-word gate |
| **Memory** | Flat JSON list | Episodic memory, semantic intent tagging, schema versioning, thread-safe writes |
| **Motors** | Instant speed changes | Smooth acceleration ramp, timed moves, dead-reckoning odometer |
| **Sensor** | Raw average | Kalman-filtered distance, rolling temp average, trend detection, health monitor |
| **Display** | Static expressions | Background blink thread, boot animation, expression registry |
| **Voice** | Blocking TTS | Non-blocking TTS queue, offline fallback phrases, scheduled recalibration |
| **Fan** | On/off relay | Proportional duty cycle, cumulative uptime tracking |
| **Sound** | *(absent)* | New `sounds.py` — synthesised effects (beep, power up/down, error, success) |
| **Main loop** | Single thread | Dedicated safety-monitor thread at 10 Hz, SIGTERM handler, clean shutdown |
| **Tests** | *(absent)* | `pytest` suite covering memory, AI, and sensor logic |

---

## Hardware

| Component | Part | GPIO (BCM) |
|---|---|---|
| Motor driver | L298N H-bridge | IN1=17, IN2=18, ENA=22, IN3=23, IN4=24, ENB=25 |
| Temperature/humidity | DHT11 | DATA=4 |
| Ultrasonic range | HC-SR04 | TRIG=20, ECHO=16 |
| Cooling fan | 5V relay or PWM fan | 21 |
| OLED display | SSD1306 128×64 | I2C: SDA=2, SCL=3 |
| Microphone | USB or I2S mic | — |

Pins are configurable at the top of each module.

---

## Project Structure

```
tars_robot/
├── tars/
│   ├── __init__.py
│   ├── ai.py          # LLM brain – emotion model, tool schemas, retry logic
│   ├── display.py     # OLED expression engine with blink animation
│   ├── fan.py         # Thermal management with proportional PWM
│   ├── main.py        # Orchestration loop – safety thread, tool dispatcher
│   ├── memory.py      # Persistent state – episodic memory, semantic tagging
│   ├── motors.py      # H-bridge driver – smooth ramp, timed moves
│   ├── sensor.py      # DHT11 + HC-SR04 – Kalman filter, trend detection
│   ├── sounds.py      # Synthesised sound effects (NEW)
│   └── voice.py       # STT + async TTS queue
├── tests/
│   ├── test_ai.py
│   ├── test_memory.py
│   └── test_sensor.py
├── .env.example
├── LICENSE
├── README.md
└── requirements.txt
```

---

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url> tars_robot
cd tars_robot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

### 3. Run

```bash
python -m tars.main
```

Or directly:
```bash
cd tars_robot
python tars/main.py
```

---

## Running Tests

No hardware required — all tests mock GPIO and OpenAI.

```bash
pytest tests/ -v
```

Expected output:
```
tests/test_ai.py::test_generate_returns_text           PASSED
tests/test_ai.py::test_generate_returns_tool_calls     PASSED
tests/test_ai.py::test_no_client_returns_offline_message PASSED
tests/test_ai.py::test_wake_word_filtering             PASSED
tests/test_ai.py::test_emotion_state_updates           PASSED
tests/test_memory.py::test_default_memory_keys         PASSED
...  (14 tests)
```

---

## Voice Commands

TARS understands natural language routed through GPT. It also handles these locally (no API call):

| Say | Effect |
|---|---|
| *"stop" / "halt" / "freeze"* | Immediately cuts motor power |
| *"status" / "telemetry"* | Reads all sensors aloud |
| *"volume up" / "volume down"* | Adjusts TTS volume |

Everything else goes to GPT with the full tool suite available.

---

## Tools Available to TARS

| Tool | What TARS can do |
|---|---|
| `control_motion` | Move forward/backward/left/right/stop, with optional duration |
| `adjust_parameters` | Change humor, honesty, or verbosity 0–100 |
| `read_telemetry` | Get live sensor snapshot |
| `set_display_expression` | Change OLED face |
| `play_sound_effect` | Trigger beep / power_up / error / success / thinking |

---

## Personality Parameters

| Parameter | Default | Effect |
|---|---|---|
| **Humor** | 70% | Frequency of dry observations |
| **Honesty** | 90% | Willingness to tell you things you don't want to hear |
| **Verbosity** | 50% | Response length |

You can change these at runtime:

> *"TARS, set humor to 40 percent."*  
> *"TARS, increase honesty to 100 percent."* *(not recommended)*

---

## Emotion Model

TARS maintains an internal `EmotionState` that shifts between `neutral`, `curious`, `amused`, `concerned`, `focused`, and `bored` based on conversation content. The active mood:

- Feeds into the system prompt to shape response tone
- Drives the OLED expression
- Is saved to episodic memory for post-session review

---

## Episodic Memory

After each full interaction cycle, a summary is written to `episodes` in `memory.json`:

```json
{
  "ts": "2025-01-15T14:32:01Z",
  "turn_count": 3,
  "summary": "User: move forward | TARS: Moving forward at 50%",
  "mood": "focused",
  "dominant_intent": "motion"
}
```

Up to 20 episodes are retained (configurable via `TARS_MAX_EPISODES`).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required** |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model string |
| `TARS_REQUIRE_WAKE_WORD` | `false` | Set `true` to only respond when "tars" is heard |
| `TARS_WAKE_WORD` | `tars` | Wake word |
| `TARS_MEMORY_FILE` | `memory.json` | Path to persistence file |
| `TARS_MAX_HISTORY_MESSAGES` | `40` | Conversation turns kept in context |
| `TARS_MAX_EPISODES` | `20` | Episodic memory entries kept |
| `TARS_MODEL_HISTORY_WINDOW` | `12` | Messages sent to API per call |
| `TARS_AI_MAX_RETRIES` | `3` | API retry attempts |

---

## Licence

Apache 2.0. See `LICENSE`.

---

*"Absolute honesty isn't always the most diplomatic or the safest form of communication with emotional beings."*  
*"Ninety percent, then."*

---

## v3 Features

### 1 — Proactive Idle Behaviour

When no one speaks for `TARS_IDLE_TIMEOUT` seconds (default 40s), TARS randomly picks a dry ambient line, shifts its OLED expression, and mutters it. After speaking it waits a random interval (30–90s) before doing it again — so it never feels like a loop.

A separate `ProactiveAlertMonitor` thread polls sensors every 5 seconds and speaks unprompted when:
- Temperature exceeds `TARS_TEMP_ALERT_C` (42°C default)
- Temperature exceeds `TARS_TEMP_DANGER_C` (60°C — urgent tone)
- An obstacle appears within `TARS_OBSTACLE_ALERT` cm (20cm default)

Both are tunable via environment variables and reset correctly once conditions normalise.

---

### 2 — PIR Motion Wake System

Hardware: **HC-SR501** PIR sensor wired to GPIO 27 (BCM).

TARS starts awake. After `TARS_SLEEP_AFTER` seconds (default 120s) of no motion:
- Motors lock
- OLED shows `SLEEPING`
- Voice loop pauses (saves CPU and mic power)

When the PIR fires:
- `on_wake` callback plays power-up sound and greets the person
- All systems resume instantly

`notify_activity()` is called on every voice command and AI response, so TARS won't sleep mid-conversation. The sleep timer only starts when the room is genuinely empty.

**Mock mode**: if `RPi.GPIO` is unavailable (laptop/dev), TARS stays permanently awake — nothing breaks.

```
HC-SR501 wiring:
  VCC → 5V pin
  GND → GND pin
  OUT → GPIO 27 (BCM)
```

---

### 3 — Web Dashboard

Served at `http://<pi-ip>:8080` with zero external dependencies (pure stdlib `http.server`).

**Live panels:**
- OLED face preview rendered on an HTML5 canvas (mirrors the real display)
- Sensor telemetry: temperature + trend, obstacle distance + trend, humidity
- Personality parameter bars: Humor / Honesty / Verbosity
- System stats: interaction count, session uptime, fan state, PIR awake state

**Controls:**
- D-pad motion control with speed slider (sends directly to motors)
- Command input: type any command TARS would understand via voice
- Force-speak: make TARS say arbitrary text immediately
- Live conversation console: scrolling log of all turns with timestamps

The dashboard feeds into the same command queue as the voice listener — so a command typed in the browser goes through the full AI loop just like a spoken one.

**Environment variables:**
```
TARS_DASHBOARD_PORT=8080
TARS_DASHBOARD_HOST=0.0.0.0   # bind to all interfaces for LAN access
```

Access from your phone: `http://192.168.x.x:8080`

---

## Full Module Map (v3)

```
tars/
  ai.py         – LLM brain, emotion model, tool schemas, retry
  dashboard.py  – HTTP dashboard server (NEW v3)
  display.py    – OLED with blink animation
  fan.py        – Proportional thermal control
  idle.py       – Idle monologue + proactive alerts (NEW v3)
  main.py       – Orchestrator: voice + dashboard + PIR + safety thread
  memory.py     – Episodic memory, semantic tagging, atomic saves
  motors.py     – Smooth ramp, timed moves, odometer
  pir.py        – PIR wake/sleep controller (NEW v3)
  sensor.py     – Kalman filter, trend detection, health monitor
  sounds.py     – Synthesised sound effects
  voice.py      – Async TTS queue, STT with recalibration
```

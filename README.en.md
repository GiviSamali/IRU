# IRU — Intelligent Remote Utility

Russian version: [README.md](README.md)

---

An AI agent for managing computers through natural language.
A local client (Windows/Linux) connects via WebSocket to a cloud server,
receives commands from an LLM (DeepSeek), and executes them in PowerShell/bash.
The user describes a task in the browser — IRU decides which commands to run
and returns the result.

---

## Features

- Execute commands on a PC via natural language
- Plan mode — step-by-step autonomous execution of complex tasks (create_plan / mark_step pipeline with live progress in the UI)
- Two-stage task classification: fast keyword matching + LLM classifier (PLAN / SIMPLE)
- Plans: free (30 commands/day, 1 device, 1 trial Plan run), pro (unlimited, dev_mode), business (unlimited, dev_mode, up to 9999 devices)
- Broadcast — send a single command to all connected devices
- File explorer — browse and download files from a device
- Memory: automatic saving of facts about the user and device, suggestions to remember new facts
- Security system: blocking dangerous commands (format, diskpart, etc.), confirmation for deletions
- Voice input (Web Speech API, ru-RU)
- Text file attachments in messages (up to 5 files, 500 KB each)
- Admin panel: user management, audit log, plan changes, device profiles
- Training data collection with user consent

---

## Architecture

```
+--------------+     HTTPS/WSS      +---------------+     WebSocket      +------------+
|  Browser UI  | <----------------> |  Server       | <----------------> |  Agent     |
|  (SPA)       |                    |  (FastAPI)    |                    |  (Python)  |
+--------------+                    |               |     DeepSeek API   +------------+
                                    |  SQLite DB    | <---------------->
                                    |  controller   |     LLM
                                    +---------------+
```

### Message flow

1. User sends text from the UI
2. Server calls `classify_task_complexity(message)`:
   - Stage 1: keyword matching ("plan", "step by step") — instant PLAN
   - Stage 2: LLM classifier (DeepSeek, temperature=0, max_tokens=100) — PLAN or SIMPLE
3. SIMPLE — LLM loop: LLM generates tool_call, server sends command to agent via WebSocket, result returns to LLM, loop repeats (up to 20 iterations)
4. PLAN — UI shows a suggestion to start Plan mode. On confirmation, LLM calls `create_plan(goal, steps)`, then sequentially executes steps with `mark_step(task_id, idx, status)`. UI updates in real time

### Components

| Component | Path | Description |
|-----------|------|-------------|
| Server | `server/main.py` | FastAPI, REST API, WebSocket hub, auth, rate limiting |
| Controller | `server/controller.py` | LLM loop (DeepSeek), system prompt, tool-call loop, pipeline |
| Database | `server/database.py` | SQLite: users, chats, messages, tasks, device_memory, audit_log |
| Auth | `server/auth.py` | JWT tokens (access + refresh) |
| Agent | `agent/agent.py` | WebSocket client, command execution via subprocess |
| UI | `ui/app.js` | SPA (no frameworks), live progress, Plan mode, file explorer |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies: FastAPI, Uvicorn, websockets, httpx, python-multipart.

### 2. Configure the LLM

Create `server/llm_config.json`:

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "model_reasoner": "deepseek-reasoner",
  "max_tokens": 4096,
  "temperature": 0.0,
  "tavily_api_key": "tvly-..."
}
```

`llm_config.json` is in `.gitignore`. The API key can be overridden via the `DEEPSEEK_API_KEY` environment variable.

| Field | Required | Description |
|-------|:---:|-------------|
| `api_key` | yes | DeepSeek API key |
| `base_url` | yes | API base URL |
| `model` | no | Model for regular requests (default: `deepseek-chat`) |
| `model_reasoner` | no | Model for Plan and autonomous modes (default: `deepseek-reasoner`) |
| `max_tokens` | no | Max tokens in response (default: 4096) |
| `temperature` | no | Generation temperature (default: 0.0, base model only) |
| `tavily_api_key` | no | Tavily API key for the `web_search` tool |

### 3. Start the server

```bash
cd server
python main.py
```

The server starts at `http://localhost:8000`. On first launch, an SQLite database (`iru.db`) and an admin user are created (the token is printed to the console).

### 4. Log in to the UI

Open `http://localhost:8000` and enter the admin token.

### 5. Start the agent

Edit `agent/config.json`:

```json
{
  "device_id": "MY_PC",
  "server_url": "ws://127.0.0.1:8000",
  "user_token": "your-token"
}
```

```bash
cd agent
python agent.py
```

The agent will connect to the server and appear in the device list.

---

## Plans (tiers)

| | free | pro | business |
|---|---|---|---|
| Commands per day | 30 | unlimited | unlimited |
| Devices | 1 | unlimited | unlimited (up to 9999) |
| Plan mode | 1 trial run | unlimited | unlimited |
| dev_mode (raw commands) | no | yes | yes |

Trial Plan run for the free tier: the user can try Plan mode once. After use, the `plan_trial_used` flag is set to 1 and further runs require a tier upgrade.

---

## VPS deployment

### Requirements

- Ubuntu/Debian, Python 3.11+
- Ports 80, 443 (HTTPS via Caddy)

### Installation

```bash
mkdir -p /opt/iru/app
cd /opt/iru
python3 -m venv venv
source venv/bin/activate
pip install -r app/requirements.txt
```

### HTTPS via Caddy

```bash
apt install caddy
```

`/etc/caddy/Caddyfile`:

```
your-domain.com {
    reverse_proxy localhost:8000
}
```

```bash
systemctl enable caddy
systemctl restart caddy
```

### Autostart (systemd)

`/etc/systemd/system/iru.service`:

```ini
[Unit]
Description=IRU Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/iru/app/server
ExecStart=/opt/iru/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable iru
systemctl start iru
```

### Updating

```bash
cd /opt/iru/app && git pull origin main && systemctl restart iru
```

---

## User management

### Via the admin panel in the UI

Log in with the admin token and open the admin panel:
- Create and delete users
- Change tier (free / pro / business)
- Copy token
- View audit log

### Via API

```bash
# Create a user
curl -X POST http://localhost:8000/api/admin/users \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Name"}'

# List users
curl http://localhost:8000/api/admin/users \
  -H "Authorization: Bearer <jwt>"
```

---

## File structure

```
├── server/
│   ├── main.py           # FastAPI, API, WebSocket hub, security
│   ├── controller.py     # LLM loop, prompt, tools, pipeline
│   ├── database.py       # SQLite: schema, migrations, PLAN_LIMITS
│   ├── auth.py           # JWT auth
│   ├── llm_config.json   # DeepSeek API config (in .gitignore)
│   └── iru.db            # Database (created at startup)
├── agent/
│   ├── agent.py          # WebSocket client, subprocess
│   ├── config.json       # device_id, server_url, user_token
│   └── platforms/        # Platform modules (windows.py, linux.py)
├── ui/
│   ├── index.html        # HTML skeleton
│   ├── app.js            # SPA logic
│   └── style.css         # Styles, dark theme
├── deploy/               # Deployment scripts, systemd, Caddy
├── landing/              # Landing page
├── requirements.txt      # Python dependencies
└── README.md
```

---

## Technologies

- Python 3.11+, FastAPI, Uvicorn, SQLite (WAL)
- DeepSeek Chat / DeepSeek Reasoner (OpenAI-compatible API)
- Tavily API (web search)
- websockets, httpx
- HTML/CSS/JS (SPA, no frameworks), JetBrains Mono
- Caddy (HTTPS), systemd

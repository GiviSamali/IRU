# IRU Development Guide

Russian version: [CONTRIBUTING.md](CONTRIBUTING.md)

Rules, pitfalls, and architectural decisions.
Read before making any code changes.

---

## Project philosophy

- "Teach the machine to be a machine" — no user action emulation (pyautogui, screenshots, clicks). Only programmatic interfaces: COM, WMI, UI Automation API, DevTools Protocol.
- The agent must be simple and reliable. Minimal changes on the agent side — all logic lives on the server.
- Universal CMD approach: the LLM decides which commands to execute via cmd/PowerShell.

---

## Local environment

### Requirements

- Python 3.11+
- Git

### Installation

```bash
git clone <repo-url>
cd iru
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### LLM configuration

Create `server/llm_config.json` (this file is in `.gitignore`):

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com/v1"
}
```

Or set via environment variable:

```bash
export DEEPSEEK_API_KEY=sk-...
```

Additional variables:
- `DEEPSEEK_API_KEY` — DeepSeek API key (takes precedence over llm_config.json)
- `tavily_api_key` in llm_config.json — Tavily key for the web_search tool

### Running the dev server

```bash
cd server
python main.py
```

The server starts at `http://localhost:8000` with `reload=True` (auto-reload on file changes).
On first launch, `iru.db` is created along with an admin user (token printed to the console).

### Running the agent locally

Edit `agent/config.json`:

```json
{
  "device_id": "DEV_PC",
  "server_url": "ws://127.0.0.1:8000",
  "user_token": "admin-token-from-console"
}
```

The `config.json` file is read with `utf-8-sig` encoding (BOM-safe).

```bash
cd agent
python agent.py
```

---

## Data flow architecture

```
UI (app.js)
  |
  +- POST /nl_command          -> creates task, launches run_nl_task()
  |                               task.status = "running"
  |
  +- GET /api/tasks/{id}       <- polls every 800ms, reads task.status
  |
  +- POST /api/tasks/{id}/confirm  -> after user confirmation
     POST /api/tasks/{id}/deny     -> after user rejection

Server (main.py)
  |
  +- run_nl_task()             -> classify_task_complexity() -> PLAN or SIMPLE
  |   +- SIMPLE: run_on_device() -> process_nl_command() from controller.py
  |   +- PLAN: UI shows suggestion -> /api/run_plan/{chat_id}
  |
  +- process_nl_command()      -> LLM loop (MAX_ITERATIONS=20)
  |   +- Iteration: LLM -> tool_call -> send_command_fn() -> result -> LLM
  |   +- except "CONFIRM_REQUIRED" -> raise ConfirmationRequired()
  |   +- except "BLOCKED"          -> error in tool_result -> LLM informs the user
  |
  +- send_command_to_agent()   -> checks safety, sends to agent via WS
      +- is_command_safe()     -> False = BLOCKED (RuntimeError)
      +- needs_confirmation()  -> True  = CONFIRM_REQUIRED (RuntimeError)

Agent (agent.py)
  |
  +- WebSocket <- receives commands, runs subprocess, returns result
```

---

## Project files

| File | Contents | When to edit |
|------|----------|--------------|
| `server/main.py` | FastAPI, endpoints, WS hub, security, tasks | API, auth, new endpoints |
| `server/controller.py` | LLM loop, prompt, tools, pipeline (create_plan/mark_step) | LLM prompt, tool-call logic, new tools |
| `server/database.py` | SQLite: schema, migrations, PLAN_LIMITS | DB schema, new tables/columns |
| `server/auth.py` | JWT access/refresh tokens | Auth |
| `agent/agent.py` | WebSocket client, subprocess, encoding | Command execution |
| `ui/app.js` | SPA logic, polling, rendering, chats, explorer, live progress | UI features |
| `ui/style.css` | Styles, dark theme, responsive layout | Appearance |
| `ui/index.html` | HTML skeleton | Only if page structure changes |

---

## VPS deploy

### Updating

```bash
cd /opt/iru/app && git pull origin main && systemctl restart iru
```

### Checking logs

```bash
journalctl -u iru --no-pager | tail -50
journalctl -u iru --since "10 min ago" --no-pager | grep -v "GET /api"
journalctl -u iru -f
```

### Environment variables on VPS

API key via systemd override:

```bash
systemctl edit iru
# [Service]
# Environment=DEEPSEEK_API_KEY=sk-...
```

Caddy (ports 80/443), UFW, and fail2ban should be active.

---

## Commit style

Commits are in Russian in the format `type: description`.

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `style`.

Examples:

```
feat: двухэтапная классификация задачи (PLAN/SIMPLE) до основного цикла
fix: business-тариф получает доступ к режиму План наравне с pro
docs: синхронизация README и CONTRIBUTING с актуальным состоянием
```

---

## Branches and PRs

- `main` — production branch, deployed to VPS
- For new features and fixes: create a branch from `main`, open a PR to `main`
- PR description in Russian: what was done, what to check

---

## Pattern: adding an LLM tool

LLM tools are defined in `server/controller.py` in the `TOOLS` array.

1. Add the tool description to `TOOLS` (OpenAI function calling format):

```python
{
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Description for the LLM",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."}
            },
            "required": ["param1"]
        }
    }
}
```

2. Add handling in the LLM loop (`process_nl_command`, in the `elif fn_name == "my_tool"` block)
3. If the tool executes a command on a device — use `send_command_fn(device_id, action, params)`
4. Return the result as a dict — it is serialized to JSON and added as a tool message

Existing tools: `execute_cmd`, `write_content`, `get_file_link`, `web_search`, `create_plan`, `mark_step`, `remember_fact`, `forget_fact`.

---

## Pattern: pipeline (create_plan / mark_step)

Plan mode works as follows:

1. `classify_task_complexity()` identifies the task as PLAN
2. UI shows a suggestion to start Plan mode (banner in chat)
3. User confirms — `/api/run_plan/{chat_id}` is called
4. LLM calls `create_plan(goal, steps)` — creates a task in the DB, UI receives the step list via `_push_tasks_view()`
5. LLM executes each step and calls `mark_step(task_id, idx, status, summary)`
6. `_push_tasks_view()` updates the UI in real time
7. When all steps are done/failed/skipped — the task is automatically closed

Live progress: `_set_current_step(text)` updates the current action text in the task object; the UI displays it during polling.

---

## Pattern: database migrations

Migrations are executed via `ALTER TABLE` in `init_db()` (`server/database.py`). Each migration is wrapped in try/except — already-applied migrations are silently skipped on subsequent runs.

How to add a new column:

1. Add `ALTER TABLE` to the `migrations` list in `init_db()`:

```python
migrations = [
    # ... existing migrations ...
    "ALTER TABLE users ADD COLUMN new_field TEXT DEFAULT ''",
]
```

2. Add the column to `CREATE TABLE` (for clean installations)
3. Add corresponding read/write functions

Example: this is how the `plan_trial_used` column was added:
- `ALTER TABLE users ADD COLUMN plan_trial_used INTEGER DEFAULT 0` in migrations
- `get_plan_trial_used(user_id)` and `set_plan_trial_used(user_id, value)` — access functions

Migrations do not use `PRAGMA table_info` directly — the approach is "try ALTER, ignore if exists".

---

## Critical rules

### 1. escapeHTML() before innerHTML

All dynamic content — commands, responses, names — must go through `escapeHTML()` before innerHTML insertion. No exceptions.

```js
// Correct:
const cmdText = escapeHTML(c.command || '');

// Wrong:
const cmdText = c.command.startsWith('[') ? c.command : escapeHTML(c.command);
```

### 2. Poll loops: stopped flag

Every poll loop with setTimeout must use an explicit `stopped` flag.

```js
let stopped = false;
const poll = async () => {
  if (stopped) return;
  // ...
  if (task.status === 'done' || task.status === 'confirm') {
    stopped = true;
    return;
  }
  if (!stopped) setTimeout(poll, 800);
};
```

### 3. Index-based loops for onclick handlers

When rendering elements in a loop that need an index in onclick — use an index-based `for` loop, not `for...of`.

### 4. UTF-8 encoding in PowerShell

Every PowerShell command must start with:
```
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8;
```
This is specified in the LLM prompt and in agent.py (chcp 65001). Without it, Russian text arrives garbled.

### 5. Onboarding — history filtering

When devices are connected, old onboarding responses ("no connected devices") poison the LLM context. Solution: `build_chat_messages(history, filter_onboarding=True)` filters messages containing markers from `ONBOARDING_MARKERS`.

### 6. CSS classes with cmd- prefix

All command classes: `.cmd-log`, `.cmd-entry`, `.cmd-summary`, `.cmd-text`, `.cmd-status`, `.cmd-details`, `.cmd-device`. Not `.log`, `.entry` — those conflict with other styles.

### 7. FK constraints on deletion

Deletion order: `DELETE training_data -> DELETE messages -> DELETE chat`. Otherwise SQLite throws an FK constraint error.

### 8. ConfirmationRequired — exception chain

```
send_command_to_agent() -> raise RuntimeError("CONFIRM_REQUIRED: ...")
    -> controller.py except Exception -> if "CONFIRM_REQUIRED" in str(e) -> raise ConfirmationRequired(...)
    -> main.py except ConfirmationRequired -> task["status"] = "confirm"
    -> UI poll -> shows Execute/Cancel buttons
```

ConfirmationRequired inherits from Exception. For new exceptions — check `str(e)` before raising.

---

## Command security system

Two levels in `send_command_to_agent()` (main.py):

### BLOCKED (fully prohibited)

`is_command_safe(cmd)` -> False. Commands: format, diskpart, bcdedit, cipher /w, rm -rf /, etc. (`DANGEROUS_PATTERNS` array).
Result: RuntimeError, LLM informs the user "not available".

### CONFIRM_REQUIRED (requires confirmation)

`needs_confirmation(cmd)` -> True. Commands: Remove-Item, del, rmdir, Stop-Process, taskkill, shutdown, etc. (`CONFIRM_PATTERNS` array).
Result: UI shows confirmation buttons. Bypassed with `skip_confirm=True` in autonomous mode.

---

## UI

### Theme

- Background: `#0a0e17`
- Accent: `#00d4ff`
- Font: JetBrains Mono

### LLM response rules

Prompt rule: no Markdown formatting in LLM responses. Plain text, CAPS for emphasis.
`strip_markdown()` in controller.py removes Markdown from responses.
The UI does not render Markdown — do not add a Markdown parser.

### localStorage

- `iru_token` — JWT access token

---

## Agent

### Principle

The agent is a simple .exe (PyInstaller --onefile). All logic lives on the server. The agent only: connects via WS, receives commands, runs subprocess, returns stdout/stderr.

### Encoding

- `chcp 65001` at startup
- `Console.OutputEncoding = UTF8`, `Console.InputEncoding = UTF8`
- `$OutputEncoding = UTF8`

### Connection

Agents connect to the server over the internet (WSS):
`wss://domain/ws/{device_id}?user_token={token}`

---

## Known limitations

- `agent/agent.py:710` — synchronous `func(**params)` call. Blocks the event loop during command execution. Do not modify without discussion.
- The LLM prompt is primarily targeted at Windows (PowerShell). Linux support exists but is secondary.

---

## Testing

There are no automated tests. Testing is manual via the UI:
1. Start the server and agent locally
2. Send a command in the chat
3. Verify execution and response
4. For Plan mode — verify step creation and live progress

---

## Common mistakes

1. Forgot `escapeHTML` — DOM breaks on special characters
2. Forgot `stopped` flag in poll — infinite polling
3. `for...of` instead of `for(let i)` — wrong index in onclick
4. Didn't update VPS — behavior doesn't match the code
5. Deletion without FK order — SQLite constraint error
6. No UTF-8 prefix — garbled Russian text
7. Onboarding in history — LLM repeats "connect a device"
8. CSS classes without `cmd-` prefix — style conflicts

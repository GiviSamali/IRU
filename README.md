# ИРУ — Интеллектуальный Режим Управления

English version: [README.en.md](README.en.md)

ИРУ — experimental AgentOS/Agent Control system: cloud/server coordination layer + local device agents + tool registry + controlled execution.

ИРУ — не просто чат-бот. Это система управления устройствами через локальных агентов, где сервер выбирает инструменты, агент выполняет действия на устройстве, а UI показывает результат и evidence.

## Что умеет сейчас

- подключать Windows/Linux агент к серверу по WebSocket;
- активировать устройство через Device Activation v1;
- готовить managed Python runtime на устройстве;
- выполнять typed tools и записывать их в run journal;
- создавать и читать файлы через file tools;
- запускать приложения и проверять реальное GUI-окно;
- искать, проверять, фокусировать и закрывать окна;
- собирать Device Passport;
- показывать CPU/RAM/Disk/GPU/state snapshot;
- хранить локальное состояние на агенте в `IRU_HOME/state`;
- использовать Tool-Only Agent Protocol v1;
- показывать used tools в Web UI;
- работать в обычном non-pipeline loop и pipeline mode.

`execute_cmd` остается fallback-инструментом, но для известных действий приоритет у typed tools.

## Архитектура коротко

Основной control center сейчас — Web UI. FastAPI server оркестрирует задачи, LLM выбирает следующий инструмент по Tool Registry, а локальный агент выполняет agent-side actions на устройстве и хранит локальную правду о состоянии.

```
Web UI / Agent Shell future
        |
        | HTTPS
        v
Server Orchestrator
        |
        | WSS
        v
Local Agent
        |
        v
Device OS / Files / Windows / Python Runtime
```

Ключевые компоненты:

- `ui/` — браузерный control center.
- `server/main.py` — FastAPI composition root.
- `server/controller_non_pipeline.py` и `server/controller_pipeline.py` — controller loops.
- `server/tool_registry.py` — Tool Registry и compact tool metadata.
- `server/task_runtime.py` — WebSocket dispatch, device mirror, state collection helpers.
- `agent/agent.py` и `agent/core/runtime.py` — локальный WebSocket client.
- `agent/core/actions.py` — agent-side tools.

## Главные принципы

- Agent-owned local state: activation/runtime/state snapshot/passport хранятся на устройстве.
- Server as coordination layer: сервер координирует и временно зеркалирует, но не является владельцем локальной правды.
- Tool-only execution: LLM вызывает tools; пользовательский ответ тоже идет через `answer.text`.
- Typed tools before shell fallback: `device.*`, `window.*`, `app.*`, file tools и runtime tools предпочтительнее `execute_cmd`.
- Fresh evidence for real-world claims: утверждения о состоянии устройства должны опираться на текущий tool result, а не на старую историю чата.
- Lazy context: LLM получает компактный manifest и context handles, а не полные receipts/logs/artifacts по умолчанию.
- Safety / confirmation: рискованные операции должны проходить через политику и подтверждение.

## Быстрый старт

### 1. Установить server dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Настроить LLM

Создайте `server/llm_config.json`:

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "model_reasoner": "deepseek-reasoner",
  "max_tokens": 4096,
  "temperature": 0.0
}
```

`server/llm_config.json` не коммитится. API key также можно передать через `DEEPSEEK_API_KEY`.

### 3. Запустить сервер

```bash
cd server
python main.py
```

Сервер поднимает Web UI на `http://localhost:8000`. При первом запуске создается SQLite DB и admin token выводится в консоль.

### 4. Войти в UI

Откройте `http://localhost:8000` и войдите с admin token.

### 5. Настроить и запустить агент

При первом запуске агент может открыть setup flow. Для headless запуска используется config с `device_id`, `server_url` и `user_token`.

```bash
cd agent
python agent.py
```

Агент подключается к `/ws/{device_id}` и отправляет registration payload. Если есть локальный cached passport, он отправляется при reconnect.

### 6. Подготовить устройство

В Device Passport UI или через задачу:

1. выполнить `device.activate`;
2. выполнить `device.prepare_runtime`;
3. выполнить `device.refresh_state`;
4. проверить activation/runtime/state/GPU в паспорте устройства.

## Где смотреть подробнее

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — общая архитектура и поток команды.
- [docs/AGENT.md](docs/AGENT.md) — локальный агент, `IRU_HOME`, actions и reconnect.
- [docs/TOOL_ONLY_PROTOCOL.md](docs/TOOL_ONLY_PROTOCOL.md) — Tool-Only Agent Protocol v1.
- [docs/DEVICE_ACTIVATION_RUNTIME.md](docs/DEVICE_ACTIVATION_RUNTIME.md) — activation и managed Python runtime.
- [docs/DEVICE_STATE.md](docs/DEVICE_STATE.md) — Device Passport и agent-owned state cache.
- [docs/TOOLS.md](docs/TOOLS.md) — Tool Registry и категории tools.
- [docs/WINDOW_APP_OBSERVATION.md](docs/WINDOW_APP_OBSERVATION.md) — проверка GUI-окон и приложений.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — server deploy и agent build.
- [docs/ROADMAP.md](docs/ROADMAP.md) — ближайшие планы.

## Статус проекта

ИРУ — experimental / beta-ready internal prototype. Архитектура уже ориентирована на AgentOS-подход, evidence и локальных агентов, но проект не заявляет production-grade enterprise security, полноценный sandbox или зрелую политику обновления агентов.

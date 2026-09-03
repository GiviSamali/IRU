# ИРУ

ИРУ построена как AgentOS/Agent Control система: пользователь работает через Web UI или локальный Agent Shell, сервер оркестрирует задачу, LLM выбирает следующий tool, а локальный агент выполняет действия на устройстве.

## LLM / DeepSeek

Текущая интеграция использует OpenAI-compatible DeepSeek API:

```text
base_url: https://api.deepseek.com
```

Актуальные модели:

```json
{
  "model": "deepseek-v4-flash",
  "model_reasoner": "deepseek-v4-pro",
  "max_tokens": 4096,
  "temperature": 0.0,
  "reasoning_effort": "high"
}
```

`deepseek-v4-flash` используется как базовая модель для обычных/non-pipeline запросов. Для этих запросов thinking явно отключён.

`deepseek-v4-pro` используется для сложных режимов, включая pipeline/autonomous. Для него thinking явно включён, а `reasoning_effort` берётся из конфигурации (по умолчанию `high`).

API key не хранится в репозитории. Предпочтительный способ передачи — переменная окружения:

```text
DEEPSEEK_API_KEY
```

Локальный `server/llm_config.json` не коммитится.

## Архитектура

```text
User
  |
  v
Web UI / Agent Shell
  |
  | HTTPS / REST / browser session
  v
FastAPI Server
  |
  | DeepSeek API + Tool Registry
  v
Controller loop
  |
  | WSS command/result
  v
Local Agent
  |
  v
Device OS / Files / Windows / Python Runtime
```

Controller поддерживает два режима выполнения:

- **Non-pipeline** — короткий tool loop для обычных задач.
- **Pipeline** — многошаговая оркестрация с planner/worker/recovery.

Во всех режимах пользовательский финальный ответ идёт через terminal communication tool (`answer.text` или специализированный answer tool). Raw assistant prose не является пользовательским ответом.

## Запуск сервера

```bash
cd server
python main.py
```

Для deploy на VPS используйте `DEPLOYMENT.md`.

## Агент

Windows build script:

```powershell
.\deploy\build_windows.ps1 -Version 3.7 -SkipUpload
```

Production Agent Shell:

```powershell
.\deploy\build_windows.ps1 -Version 3.7 -BuildShell -ShellWebUrl "https://irumode.ru"
```

## Секреты

Не коммитьте:

- `DEEPSEEK_API_KEY`
- `server/llm_config.json`
- `server/.jwt_secret`
- локальные базы и build artifacts

Подробнее см. `DEPLOYMENT.md` и документацию архитектуры.

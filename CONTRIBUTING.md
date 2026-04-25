# Руководство по разработке ИРУ

English version: [CONTRIBUTING.en.md](CONTRIBUTING.en.md)

Правила, подводные камни и архитектурные решения.
Читать перед любыми изменениями в коде.

---

## Философия проекта

- "Научить машину быть машиной" — никакой эмуляции пользовательских действий (pyautogui, скриншоты, клики). Только программные интерфейсы: COM, WMI, UI Automation API, DevTools Protocol.
- Агент должен быть простым и безотказным. Минимум изменений на стороне агента — вся логика на сервере.
- Универсальный CMD-подход: LLM сама решает, какие команды выполнить через cmd/PowerShell.

---

## Локальное окружение

### Требования

- Python 3.11+
- Git

### Установка

```bash
git clone <repo-url>
cd iru
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### Конфигурация LLM

Создайте `server/llm_config.json` (файл в `.gitignore`):

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com/v1"
}
```

Или задайте через переменную окружения:

```bash
export DEEPSEEK_API_KEY=sk-...
```

Дополнительные переменные:
- `DEEPSEEK_API_KEY` — ключ DeepSeek API (приоритет над llm_config.json)
- `tavily_api_key` в llm_config.json — ключ Tavily для инструмента web_search

### Запуск dev-сервера

```bash
cd server
python main.py
```

Сервер стартует на `http://localhost:8000` с `reload=True` (auto-reload при изменениях).
При первом запуске создаётся `iru.db` и admin-пользователь (токен в консоли).

### Запуск агента локально

Отредактируйте `agent/config.json`:

```json
{
  "device_id": "DEV_PC",
  "server_url": "ws://127.0.0.1:8000",
  "user_token": "admin-токен-из-консоли"
}
```

Файл `config.json` читается в кодировке `utf-8-sig` (BOM-safe).

```bash
cd agent
python agent.py
```

---

## Архитектура потока данных

```
UI (app.js)
  |
  +- POST /nl_command          -> создаёт task, запускает run_nl_task()
  |                               task.status = "running"
  |
  +- GET /api/tasks/{id}       <- poll каждые 800мс, читает task.status
  |
  +- POST /api/tasks/{id}/confirm  -> после подтверждения пользователем
     POST /api/tasks/{id}/deny     -> после отказа

Сервер (main.py)
  |
  +- run_nl_task()             -> classify_task_complexity() -> PLAN или SIMPLE
  |   +- SIMPLE: run_on_device() -> process_nl_command() из controller.py
  |   +- PLAN: UI показывает предложение -> /api/run_plan/{chat_id}
  |
  +- process_nl_command()      -> LLM-цикл (MAX_ITERATIONS=20)
  |   +- Итерация: LLM -> tool_call -> send_command_fn() -> результат -> LLM
  |   +- except "CONFIRM_REQUIRED" -> raise ConfirmationRequired()
  |   +- except "BLOCKED"          -> error в tool_result -> LLM сообщает пользователю
  |
  +- send_command_to_agent()   -> проверяет безопасность, отправляет агенту через WS
      +- is_command_safe()     -> False = BLOCKED (RuntimeError)
      +- needs_confirmation()  -> True  = CONFIRM_REQUIRED (RuntimeError)

Агент (agent.py)
  |
  +- WebSocket <- получает команды, выполняет subprocess, возвращает результат
```

---

## Файлы проекта

| Файл | Что содержит | Когда редактировать |
|------|--------------|---------------------|
| `server/main.py` | FastAPI, эндпоинты, WS-хаб, безопасность, задачи | API, авторизация, новые эндпоинты |
| `server/controller.py` | LLM-цикл, промпт, инструменты, pipeline (create_plan/mark_step) | Промпт LLM, логика tool-calls, новые инструменты |
| `server/database.py` | SQLite: схема, миграции, PLAN_LIMITS | Схема БД, новые таблицы/колонки |
| `server/auth.py` | JWT access/refresh токены | Авторизация |
| `agent/agent.py` | WebSocket клиент, subprocess, кодировка | Выполнение команд |
| `ui/app.js` | SPA логика, poll, рендер, чаты, проводник, live-прогресс | Интерфейс |
| `ui/style.css` | Стили, тёмная тема, адаптив | Внешний вид |
| `ui/index.html` | HTML-каркас | Только если меняется структура страницы |

---

## VPS deploy

### Обновление

```bash
cd /opt/iru/app && git pull origin main && systemctl restart iru
```

### Проверка логов

```bash
journalctl -u iru --no-pager | tail -50
journalctl -u iru --since "10 min ago" --no-pager | grep -v "GET /api"
journalctl -u iru -f
```

### Переменные окружения на VPS

API-ключ через systemd override:

```bash
systemctl edit iru
# [Service]
# Environment=DEEPSEEK_API_KEY=sk-...
```

Caddy (80/443), UFW, fail2ban должны быть активны.

---

## Стиль коммитов

Коммиты на русском языке в формате `тип: описание`.

Типы: `feat`, `fix`, `refactor`, `docs`, `chore`, `style`.

Примеры:

```
feat: двухэтапная классификация задачи (PLAN/SIMPLE) до основного цикла
fix: business-тариф получает доступ к режиму План наравне с pro
docs: синхронизация README и CONTRIBUTING с актуальным состоянием
refactor: вынос JWT-логики в auth.py
```

---

## Ветки и PR

- `main` — production-ветка, деплоится на VPS
- Для новых фич и исправлений: отдельная ветка от `main`, PR в `main`
- PR-описание на русском: что сделано, что проверить

---

## Паттерн: добавление LLM-инструмента

LLM-инструменты определены в `server/controller.py` в массиве `TOOLS`.

1. Добавить описание инструмента в `TOOLS` (формат OpenAI function calling):

```python
{
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Описание для LLM",
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

2. Добавить обработку в LLM-цикле (`process_nl_command`, блок `elif fn_name == "my_tool"`)
3. Если инструмент выполняет команду на устройстве — использовать `send_command_fn(device_id, action, params)`
4. Результат вернуть как dict — он сериализуется в JSON и добавляется как tool message

Существующие инструменты: `execute_cmd`, `write_content`, `get_file_link`, `web_search`, `create_plan`, `mark_step`, `remember_fact`, `forget_fact`.

---

## Паттерн: pipeline (create_plan / mark_step)

Режим План работает так:

1. `classify_task_complexity()` определяет задачу как PLAN
2. UI показывает предложение запустить План (плашка в чате)
3. Пользователь подтверждает — вызывается `/api/run_plan/{chat_id}`
4. LLM вызывает `create_plan(goal, steps)` — создаёт задачу в БД, UI получает список шагов через `_push_tasks_view()`
5. LLM выполняет каждый шаг и вызывает `mark_step(task_id, idx, status, summary)`
6. `_push_tasks_view()` обновляет UI в реальном времени
7. Когда все шаги done/failed/skipped — задача автоматически закрывается

Live-прогресс: `_set_current_step(text)` обновляет текст текущего действия в task-объекте, UI отображает его при polling.

---

## Паттерн: миграции БД

Миграции выполняются через `ALTER TABLE` в `init_db()` (`server/database.py`). Каждая миграция обёрнута в try/except — при повторном запуске уже применённые миграции тихо пропускаются.

Как добавить новую колонку:

1. Добавить `ALTER TABLE` в список `migrations` в `init_db()`:

```python
migrations = [
    # ... существующие миграции ...
    "ALTER TABLE users ADD COLUMN new_field TEXT DEFAULT ''",
]
```

2. Добавить колонку в `CREATE TABLE` (для чистой установки)
3. Добавить соответствующие функции чтения/записи

Пример: так была добавлена колонка `plan_trial_used`:
- `ALTER TABLE users ADD COLUMN plan_trial_used INTEGER DEFAULT 0` в миграциях
- `get_plan_trial_used(user_id)` и `set_plan_trial_used(user_id, value)` — функции доступа

Миграции через `PRAGMA table_info` не используются напрямую — используется подход "try ALTER, ignore if exists".

---

## Критические правила

### 1. escapeHTML() перед innerHTML

Любой динамический контент — команды, ответы, имена — обязательно через `escapeHTML()` перед вставкой в innerHTML. Без исключений.

```js
// Правильно:
const cmdText = escapeHTML(c.command || '');

// Неправильно:
const cmdText = c.command.startsWith('[') ? c.command : escapeHTML(c.command);
```

### 2. Poll-циклы: флаг stopped

При любом poll-цикле с setTimeout — использовать явный флаг `stopped`.

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

### 3. Индексы в onclick внутри циклов

Если рендеришь элементы в цикле и нужен индекс в onclick — используй индексный `for`, не `for...of`.

### 4. Кодировка UTF-8 в PowerShell

Каждая PowerShell-команда должна начинаться с:
```
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8;
```
Это прописано в промпте LLM и в agent.py (chcp 65001). Без этого русский текст приходит искажённым.

### 5. Онбординг — фильтрация истории

Когда устройства подключены, старые онбординговые ответы ("нет подключённых устройств") отравляют контекст LLM. Решение: `build_chat_messages(history, filter_onboarding=True)` фильтрует сообщения с маркерами из `ONBOARDING_MARKERS`.

### 6. CSS-классы с префиксом cmd-

Все классы команд: `.cmd-log`, `.cmd-entry`, `.cmd-summary`, `.cmd-text`, `.cmd-status`, `.cmd-details`, `.cmd-device`. Не `.log`, `.entry` — конфликтует с другими стилями.

### 7. FK-ограничения при удалении

Порядок удаления: `DELETE training_data -> DELETE messages -> DELETE chat`. Иначе SQLite выдаст FK constraint error.

### 8. ConfirmationRequired — цепочка исключений

```
send_command_to_agent() -> raise RuntimeError("CONFIRM_REQUIRED: ...")
    -> controller.py except Exception -> if "CONFIRM_REQUIRED" in str(e) -> raise ConfirmationRequired(...)
    -> main.py except ConfirmationRequired -> task["status"] = "confirm"
    -> UI poll -> кнопки Выполнить/Отменить
```

ConfirmationRequired наследуется от Exception. Новые исключения — проверять строку `str(e)` перед raise.

---

## Система безопасности команд

Два уровня в `send_command_to_agent()` (main.py):

### BLOCKED (полный запрет)

`is_command_safe(cmd)` -> False. Команды: format, diskpart, bcdedit, cipher /w, rm -rf /, и др. (массив `DANGEROUS_PATTERNS`).
Результат: RuntimeError, LLM сообщает пользователю "недоступно".

### CONFIRM_REQUIRED (требует подтверждения)

`needs_confirmation(cmd)` -> True. Команды: Remove-Item, del, rmdir, Stop-Process, taskkill, shutdown и др. (массив `CONFIRM_PATTERNS`).
Результат: UI показывает кнопки подтверждения. Обходится флагом `skip_confirm=True` в автономном режиме.

---

## UI

### Тема

- Фон: `#0a0e17`
- Акцент: `#00d4ff`
- Шрифт: JetBrains Mono

### Правила LLM-ответов

Правило промпта: никакой Markdown-разметки в ответах LLM. Чистый текст, CAPS для акцента.
`strip_markdown()` в controller.py убирает Markdown из ответов.
UI не рендерит Markdown — не добавлять Markdown-парсер.

### localStorage

- `iru_token` — JWT access token

---

## Агент

### Принцип

Агент — простой .exe (PyInstaller --onefile). Вся логика на сервере. Агент только: подключается по WS, получает команды, выполняет subprocess, возвращает stdout/stderr.

### Кодировка

- `chcp 65001` при старте
- `Console.OutputEncoding = UTF8`, `Console.InputEncoding = UTF8`
- `$OutputEncoding = UTF8`

### Подключение

Агенты подключаются через интернет к серверу (WSS):
`wss://домен/ws/{device_id}?user_token={token}`

---

## Известные ограничения

- `agent/agent.py:710` — синхронный вызов `func(**params)`. Блокирует event loop на время выполнения команды. Не трогать без обсуждения.
- Промпт LLM ориентирован на Windows (PowerShell). Поддержка Linux есть, но вторична.

---

## Тестирование

Автоматических тестов нет. Тестирование ручное через UI:
1. Запустить сервер и агента локально
2. Отправить команду в чат
3. Проверить выполнение и ответ
4. Для режима План — проверить создание шагов и live-прогресс

---

## Частые ошибки

1. Забыл `escapeHTML` — DOM ломается при спецсимволах
2. Забыл `stopped`-флаг в poll — бесконечный poll
3. `for...of` вместо `for(let i)` — неправильный индекс в onclick
4. Не обновил VPS — поведение не совпадает с кодом
5. Удаление без FK-порядка — SQLite constraint error
6. Нет UTF-8 префикса — кракозябры в русском тексте
7. Онбординг в истории — LLM повторяет "подключите устройство"
8. CSS-классы без `cmd-` префикса — конфликт стилей

# Руководство по разработке ИРУ

Правила, подводные камни и архитектурные решения.
Читать перед любыми изменениями в коде.

---

## Философия проекта

- "Научить машину быть машиной" — никакой эмуляции пользовательских действий (pyautogui, скриншоты, клики). Только программные интерфейсы: COM, WMI, UI Automation API, DevTools Protocol.
- Агент должен быть простым и безотказным. Минимум изменений на стороне агента — вся логика на сервере.
- Универсальный CMD-подход: не прописываем каждое действие, а даём LLM свободу взаимодействия через cmd/PowerShell.
- Сейчас рассматриваем ОС Windows.

---

## Архитектура потока данных

```
UI (app.js)
  │
  ├─ POST /nl_command          → создаёт task, запускает run_nl_task()
  │                               task.status = "running"
  │
  ├─ GET /api/tasks/{id}       ← poll каждые 1с, читает task.status
  │
  └─ POST /api/tasks/{id}/confirm  → после подтверждения пользователем
     POST /api/tasks/{id}/deny     → после отказа

Сервер (main.py)
  │
  ├─ run_nl_task()             → вызывает run_on_device()
  │   └─ run_on_device()       → вызывает process_nl_command() из controller.py
  │       ├─ Успех             → result.status = "ok" → task.status = "done"
  │       ├─ ConfirmationRequired → result.status = "confirm" → task.status = "confirm"
  │       └─ Exception         → result.status = "error" → task.status = "error"
  │
  ├─ send_command_to_agent()   → проверяет безопасность, отправляет агенту через WS
  │   ├─ is_command_safe()     → False = BLOCKED (RuntimeError)
  │   └─ needs_confirmation()  → True  = CONFIRM_REQUIRED (RuntimeError)
  │
  └─ api_confirm_task()        → send_command_to_agent(skip_confirm=True)

Контроллер (controller.py)
  │
  └─ process_nl_command()      → LLM-цикл (MAX_ITERATIONS=5)
      ├─ Итерация: LLM → tool_call → send_command_fn() → результат → LLM
      ├─ except "CONFIRM_REQUIRED" → raise ConfirmationRequired()
      └─ except "BLOCKED"          → error в tool_result → LLM сообщает пользователю

Агент (agent.py)
  │
  └─ WebSocket ← получает команды, выполняет subprocess, возвращает результат
```

---

## Файлы проекта

| Файл | Строк | Что содержит | Когда редактировать |
|------|-------|--------------|---------------------|
| `server/main.py` | ~1400 | FastAPI, эндпоинты, WS-хаб, безопасность, задачи | API, авторизация, новые эндпоинты |
| `server/controller.py` | ~500 | LLM-цикл, промпт, инструменты, ConfirmationRequired | Промпт LLM, логика tool-calls |
| `server/database.py` | ~300 | SQLite: users, chats, messages, training_data | Схема БД, новые таблицы |
| `agent/agent.py` | ~300 | WebSocket клиент, subprocess, кодировка | Выполнение команд, кодировка |
| `ui/app.js` | ~900 | SPA логика, poll, рендер, чаты, проводник | Интерфейс, новые фичи UI |
| `ui/style.css` | ~800 | Стили, тёмная тема, адаптив | Внешний вид |
| `ui/index.html` | ~100 | HTML-каркас | Только если меняется структура |
| `server/llm_config.json` | ~6 | API-ключ, модель, температура | Смена LLM-провайдера |

---

## Критические правила (баги которые уже были)

### 1. ВСЕГДА escapeHTML() перед innerHTML

Любой динамический контент — команды, ответы, имена — ОБЯЗАТЕЛЬНО через escapeHTML() перед вставкой в innerHTML. Без исключений.

БЫЛО (сломано):
```js
const cmdText = c.command.startsWith('[') ? c.command : escapeHTML(c.command);
```
Команда `[Console]::OutputEncoding...` содержит кавычки, которые ломают DOM.

ПРАВИЛЬНО:
```js
const cmdText = escapeHTML(c.command || '');
```

### 2. Poll-циклы: флаг stopped

При любом poll-цикле с setTimeout — использовать явный флаг `stopped`. Без него poll может продолжаться после получения финального статуса.

```js
let stopped = false;
const poll = async () => {
  if (stopped) return;
  // ...
  if (task.status === 'confirm' || task.status === 'done') {
    stopped = true;
    // обработка
    return;
  }
  if (!stopped) setTimeout(poll, 1000);
};
```

### 3. Индексы в onclick внутри циклов

Если рендеришь элементы в цикле и нужен индекс в onclick — используй индексный `for`, не `for...of`.

БЫЛО (сломано):
```js
for (const m of state.messages) {   // нет индекса!
  // ...
  onclick="confirmTask('${m.confirmTaskId}', ${i})"  // i — от вложенного цикла!
}
```

ПРАВИЛЬНО:
```js
for (let mi = 0; mi < state.messages.length; mi++) {
  const m = state.messages[mi];
  // ...
  onclick="confirmTask('${m.confirmTaskId}', ${mi})"
}
```

### 4. Кодировка UTF-8 в PowerShell

Каждая команда PowerShell ДОЛЖНА начинаться с:
```
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8;
```
Это прописано в промпте LLM (правило 10) и в agent.py (chcp 65001 + Console.OutputEncoding).
Без этого русский текст приходит как "Р'Р°С€ Р»РѕРіРёРЅ".

### 5. Онбординг — фильтрация истории

Когда устройства уже подключены, старые онбординговые ответы ("нет подключённых устройств") из chat_history отравляют LLM — она начинает повторять "подключите устройство".
Решение: `build_chat_messages(history, filter_onboarding=True)` отфильтровывает сообщения содержащие 2+ маркера из `ONBOARDING_MARKERS`.

### 6. CSS-классы с префиксом cmd-

Все классы команд используют префикс `cmd-`: `.cmd-log`, `.cmd-entry`, `.cmd-summary`, `.cmd-text`, `.cmd-status`, `.cmd-details`, `.cmd-device`.
НЕ `.log`, `.entry` и т.д. — это конфликтует с другими стилями.

### 7. FK-ограничения при удалении

При удалении чата или пользователя — сначала удалить training_data, потом messages, потом chat. Иначе SQLite выдаст FK constraint error.
Порядок: `DELETE training_data → DELETE messages → DELETE chat`

### 8. ConfirmationRequired — цепочка исключений

```
send_command_to_agent() → raise RuntimeError("CONFIRM_REQUIRED: ...")
    ↓
controller.py except Exception → if "CONFIRM_REQUIRED" in str(e) → raise ConfirmationRequired(...)
    ↓
main.py except ConfirmationRequired → return {"status": "confirm", ...}
    ↓
run_nl_task() → task["status"] = "confirm"
    ↓
UI poll → показывает кнопки Выполнить/Отменить
```

ВАЖНО: ConfirmationRequired наследуется от Exception. Поэтому в controller.py он ловится через `except Exception as e:`. Если нужно добавить новые исключения — проверять строку `str(e)` ПЕРЕД raise.

---

## Система безопасности команд

Два уровня в `send_command_to_agent()` (main.py):

### BLOCKED (полный запрет)
Проверка: `is_command_safe(cmd)` → False
Команды: format, diskpart, bcdedit, cipher /w, sfc, dism, bcdboot, reagentc
Результат: RuntimeError("BLOCKED: ..."), LLM сообщает пользователю "недоступно в бета"

### CONFIRM_REQUIRED (требует подтверждения)
Проверка: `needs_confirmation(cmd)` → True
Команды: Remove-Item, del, rmdir, rd, Stop-Process, kill, taskkill, Restart-Computer, shutdown, Clear-Content, Format-Volume, Set-ExecutionPolicy, Disable-NetAdapter, reg delete, Clear-RecycleBin, Uninstall-Package
Результат: RuntimeError("CONFIRM_REQUIRED: ...") → UI кнопки → POST /confirm или /deny

Добавлять новые паттерны: массивы DANGEROUS_PATTERNS и CONFIRM_PATTERNS в main.py.

---

## Деплой на VPS

### Обновление
```bash
cd /opt/iru/app && git pull origin main && systemctl restart iru
```

### Проверка логов
```bash
# Последние 50 строк
journalctl -u iru --no-pager | tail -50

# Фильтрация полезных логов (без GET /api)
journalctl -u iru --since "10 min ago" --no-pager | grep -v "GET /api"

# Живой поток
journalctl -u iru -f
```

### Важно
- API-ключ DeepSeek хранится в systemd override: `systemctl edit iru` → Environment=DEEPSEEK_API_KEY=...
- Uvicorn запускается с `reload=True` — при git pull сервер может перезагрузиться автоматически, но лучше всегда делать `systemctl restart iru`
- Caddy (порты 80/443), UFW, fail2ban активны
- Домены: irumode.ru, irumode.online

---

## UI — ключевые моменты

### Тема
- Фон: #0a0e17
- Акцент: #00d4ff
- Шрифт: JetBrains Mono
- Только .ico для логотипа (IruIcon.ico), не .png

### localStorage
- `iru_token` — токен авторизации

### State
```js
state = {
  user,              // {id, name, token, is_admin}
  chats,             // [{id, title, ...}]
  currentChatId,
  messages,          // [{role, content, commands, loading, confirmTaskId}]
  devices,           // {device_id: {hostname, os, ...}}
  selectedDevice,
  pendingTasks,      // [{task_id, msgIndex}]
  sendTarget,        // 'single' | 'all'
}
```

### Правила LLM-ответов
Правило 12 промпта: НИКОГДА не используй Markdown-разметку. Чистый текст.
UI не рендерит Markdown — не добавлять Markdown-парсер.

---

## Агент

### Минимальные изменения
Агент должен быть простым .exe (PyInstaller --onefile). Вся логика — на сервере.
Агент только: подключается по WS, получает команды, выполняет subprocess, возвращает stdout/stderr.

### Кодировка
- `chcp 65001` при старте
- `Console.OutputEncoding = UTF8`
- `Console.InputEncoding = UTF8`
- `$OutputEncoding = UTF8`

### Подключение
Агенты подключаются через ИНТЕРНЕТ к серверу (WSS), не локально.
URL формат: `wss://irumode.ru/ws/{device_id}?user_token={token}`

---

## Частые ошибки при разработке

1. **Забыл escapeHTML** → DOM ломается при спецсимволах в командах
2. **Забыл stopped-флаг в poll** → бесконечный poll после получения результата
3. **for...of вместо for(let i)** → неправильный индекс в onclick
4. **Не обновил VPS** → код на сервере старый, поведение не совпадает с ожидаемым
5. **Удаление без FK-порядка** → SQLite constraint error
6. **Нет UTF-8 префикса** → кракозябры в русском тексте
7. **Онбординг в истории** → LLM повторяет "подключите устройство" вместо выполнения задачи
8. **HTML в cmd-классах без префикса** → конфликт стилей с другими элементами

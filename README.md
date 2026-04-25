# ИРУ — Интеллектуальный Режим Управления

English version: [README.en.md](README.en.md)

---

AI-агент для управления компьютерами через естественный язык.
Локальный клиент (Windows/Linux) подключается по WebSocket к облачному серверу,
получает команды от LLM (DeepSeek) и выполняет их в PowerShell/bash.
Пользователь описывает задачу текстом в браузере — ИРУ сама решает,
какие команды запустить, и отдаёт результат.

---

## Возможности

- Выполнение команд на ПК через естественный язык
- Режим План — пошаговое автономное исполнение сложных задач (create_plan / mark_step pipeline с live-прогрессом в UI)
- Двухэтапная классификация задач: быстрые ключевые слова + LLM-классификатор (PLAN / SIMPLE)
- Тарифы: free (30 команд/день, 1 устройство, 1 пробный запуск Плана), pro (безлимит, dev_mode), business (безлимит, dev_mode, до 9999 устройств)
- Broadcast — одна команда на все подключённые устройства
- Файловый проводник — навигация и скачивание файлов с устройства
- Память: автоматическое сохранение фактов о пользователе и устройстве, предложение запомнить новые факты
- Система безопасности: блокировка опасных команд (format, diskpart и др.), подтверждение удалений
- Голосовой ввод (Web Speech API, ru-RU)
- Вложение текстовых файлов в сообщения (до 5 файлов, 500 КБ каждый)
- Админ-панель: управление пользователями, аудит, смена тарифов, профили устройств
- Сбор training data с согласия пользователя

---

## Архитектура

```
┌─────────────┐     HTTPS/WSS      ┌──────────────┐     WebSocket      ┌───────────┐
│  Браузер UI │ <───────────────── >│  Сервер      │ <─────────────────>│  Агент    │
│  (SPA)      │                     │  (FastAPI)   │                    │  (Python) │
└─────────────┘                     │              │     DeepSeek API   └───────────┘
                                    │  SQLite БД   │ <────────────────>
                                    │  controller  │     LLM
                                    └──────────────┘
```

### Поток сообщений

1. Пользователь отправляет текст из UI
2. Сервер вызывает `classify_task_complexity(message)`:
   - Стадия 1: ключевые слова ("план", "пошагово", "по шагам") — мгновенно PLAN
   - Стадия 2: LLM-классификатор (DeepSeek, temperature=0, max_tokens=100) — PLAN или SIMPLE
3. SIMPLE — LLM-цикл: LLM генерирует tool_call, сервер отправляет команду агенту через WebSocket, результат возвращается в LLM, цикл повторяется (до 20 итераций)
4. PLAN — UI показывает предложение запустить План. При подтверждении LLM вызывает `create_plan(goal, steps)`, затем последовательно выполняет шаги с `mark_step(task_id, idx, status)`. UI обновляется в реальном времени

### Компоненты

| Компонент | Путь | Описание |
|-----------|------|----------|
| Сервер | `server/main.py` | FastAPI, REST API, WebSocket-хаб, авторизация, rate limiting |
| Контроллер | `server/controller.py` | LLM-цикл (DeepSeek), system prompt, tool-call loop, pipeline |
| База данных | `server/database.py` | SQLite: users, chats, messages, tasks, device_memory, audit_log |
| Авторизация | `server/auth.py` | JWT-токены (access + refresh) |
| Агент | `agent/agent.py` | WebSocket-клиент, выполнение команд через subprocess |
| UI | `ui/app.js` | SPA без фреймворков, live-прогресс, план, проводник |

---

## Быстрый старт

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

Зависимости: FastAPI, Uvicorn, websockets, httpx, python-multipart.

### 2. Настройка LLM

Создайте `server/llm_config.json`:

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

`llm_config.json` находится в `.gitignore`. API-ключ можно переопределить через переменную окружения `DEEPSEEK_API_KEY`.

| Поле | Обязательное | Описание |
|------|:---:|----------|
| `api_key` | да | API-ключ DeepSeek |
| `base_url` | да | Базовый URL API |
| `model` | нет | Модель для обычных запросов (по умолчанию `deepseek-chat`) |
| `model_reasoner` | нет | Модель для режима План и автономного режима (по умолчанию `deepseek-reasoner`) |
| `max_tokens` | нет | Максимум токенов в ответе (по умолчанию 4096) |
| `temperature` | нет | Температура генерации (по умолчанию 0.0, только для базовой модели) |
| `tavily_api_key` | нет | API-ключ Tavily для инструмента `web_search` |

### 3. Запуск сервера

```bash
cd server
python main.py
```

Сервер запустится на `http://localhost:8000`. При первом запуске создаётся SQLite-база `iru.db` и пользователь admin с уникальным токеном (выводится в консоль).

### 4. Вход в UI

Откройте `http://localhost:8000`, введите admin-токен.

### 5. Запуск агента

Отредактируйте `agent/config.json`:

```json
{
  "device_id": "МОЙ_ПК",
  "server_url": "ws://127.0.0.1:8000",
  "user_token": "ваш-токен"
}
```

```bash
cd agent
python agent.py
```

Агент подключится к серверу и появится в списке устройств.

---

## Тарифы

| | free | pro | business |
|---|---|---|---|
| Команд в день | 30 | безлимит | безлимит |
| Устройств | 1 | безлимит | безлимит (до 9999) |
| Режим План | 1 пробный запуск | безлимит | безлимит |
| dev_mode (raw-команды) | нет | да | да |

Пробный запуск Плана для free-тарифа: пользователь может один раз попробовать режим План. После использования флаг `plan_trial_used` выставляется в 1 и повторный запуск недоступен без смены тарифа.

---

## Развёртывание на VPS

### Требования

- Ubuntu/Debian, Python 3.11+
- Порты 80, 443 (HTTPS через Caddy)

### Установка

```bash
mkdir -p /opt/iru/app
cd /opt/iru
python3 -m venv venv
source venv/bin/activate
pip install -r app/requirements.txt
```

### HTTPS через Caddy

```bash
apt install caddy
```

`/etc/caddy/Caddyfile`:

```
ваш-домен.ru {
    reverse_proxy localhost:8000
}
```

```bash
systemctl enable caddy
systemctl restart caddy
```

### Автозапуск (systemd)

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

### Обновление

```bash
cd /opt/iru/app && git pull origin main && systemctl restart iru
```

---

## Управление пользователями

### Через админ-панель в UI

Войдите под admin-токеном, откройте админ-панель:
- Создание, удаление пользователей
- Смена тарифа (free / pro / business)
- Копирование токена
- Аудит-лог действий

### Через API

```bash
# Создать пользователя
curl -X POST http://localhost:8000/api/admin/users \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Имя"}'

# Список пользователей
curl http://localhost:8000/api/admin/users \
  -H "Authorization: Bearer <jwt>"
```

---

## Структура файлов

```
├── server/
│   ├── main.py           # FastAPI, API, WebSocket-хаб, безопасность
│   ├── controller.py     # LLM-цикл, промпт, инструменты, pipeline
│   ├── database.py       # SQLite: схема, миграции, PLAN_LIMITS
│   ├── auth.py           # JWT авторизация
│   ├── llm_config.json   # Конфиг DeepSeek API (в .gitignore)
│   └── iru.db            # БД (создаётся при запуске)
├── agent/
│   ├── agent.py          # WebSocket-клиент, subprocess
│   ├── config.json       # device_id, server_url, user_token
│   └── platforms/        # Платформенные модули (windows.py, linux.py)
├── ui/
│   ├── index.html        # HTML-каркас
│   ├── app.js            # SPA логика
│   └── style.css         # Стили, тёмная тема
├── deploy/               # Скрипты деплоя, systemd, Caddy
├── landing/              # Лендинг
├── requirements.txt      # Python-зависимости
└── README.md
```

---

## Технологии

- Python 3.11+, FastAPI, Uvicorn, SQLite (WAL)
- DeepSeek Chat / DeepSeek Reasoner (OpenAI-совместимый API)
- Tavily API (веб-поиск)
- websockets, httpx
- HTML/CSS/JS (SPA без фреймворков), JetBrains Mono
- Caddy (HTTPS), systemd

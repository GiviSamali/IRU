# ИРУ v3.4 — Интеллектуальный Режим Управления

Веб-приложение для удалённого управления компьютерами через естественный язык.
Пользователь описывает задачу текстом — ИИ (DeepSeek) переводит её в команды
PowerShell/bash и выполняет на подключённых устройствах.

---

## Архитектура

```
┌─────────────┐     HTTPS/WSS      ┌──────────────┐     WebSocket      ┌───────────┐
│  Браузер UI │ ◄──────────────────► │  Сервер      │ ◄─────────────────► │  Агент    │
│  (index.html)│                     │  (FastAPI)   │                     │  (Python) │
└─────────────┘                     │              │     DeepSeek API    └───────────┘
                                    │  SQLite БД   │ ◄──────────────────►
                                    │  controller  │     LLM
                                    └──────────────┘
```

### Компоненты

| Компонент | Путь | Описание |
|-----------|------|----------|
| **Сервер** | `server/main.py` | FastAPI, REST API, WebSocket-хаб, авторизация |
| **Контроллер** | `server/controller.py` | LLM-планировщик (DeepSeek), tool-call loop |
| **База данных** | `server/database.py` | SQLite: пользователи, чаты, сообщения, training data |
| **Агент** | `agent/agent.py` | Исполнитель команд на устройстве |
| **UI** | `ui/index.html` | Веб-интерфейс (SPA, без фреймворков) |

---

## Быстрый старт

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Настройка API-ключа DeepSeek

Откройте `server/llm_config.json` и вставьте свой API-ключ:

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "YOUR_API_KEY_HERE",
  "model": "deepseek-chat",
  "max_tokens": 1024,
  "temperature": 0.0
}
```

### 3. Запуск сервера

```bash
cd server
python main.py
```

Сервер запустится на `http://localhost:8000`.

При первом запуске автоматически:
- Создаётся файл `iru.db` (SQLite база)
- Создаётся пользователь **admin** с уникальным токеном
- Токен выводится в консоль — **сохраните его!**

```
[db] Создан admin-пользователь. Токен: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
[server] ИРУ v3.4 запущен
```

### 4. Вход в UI

1. Откройте `http://localhost:8000` в браузере
2. Введите токен admin-пользователя
3. Вы попадёте в интерфейс ИРУ

### 5. Запуск агента на устройстве

Отредактируйте `agent/config.json`:

```json
{
  "device_id": "МОЙ_ПК",
  "server_url": "ws://127.0.0.1:8000",
  "user_token": "ваш-токен-пользователя"
}
```

Запустите:

```bash
cd agent
python agent.py
```

Агент подключится к серверу и появится в списке устройств в UI.

---

## Развёртывание на VPS

### Требования

- VPS с Ubuntu/Debian, Python 3.11+
- Открытые порты: 80, 443 (для HTTPS через Caddy)

### Установка

```bash
# Создать директорию и виртуальное окружение
mkdir -p /opt/iru/app
cd /opt/iru
python3 -m venv venv
source venv/bin/activate

# Скопировать файлы проекта в /opt/iru/app/
# Установить зависимости
pip install -r app/requirements.txt
```

### HTTPS через Caddy (рекомендуется)

Caddy автоматически получает SSL-сертификаты от Let's Encrypt.

```bash
# Установить Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy
```

Создайте `/etc/caddy/Caddyfile`:

```
ваш-домен.ru {
    reverse_proxy localhost:8000
}
```

Если нет домена (только IP), используйте HTTP-режим:

```
:80 {
    reverse_proxy localhost:8000
}
```

```bash
systemctl enable caddy
systemctl restart caddy
```

### Автозапуск сервера (systemd)

Создайте `/etc/systemd/system/iru.service`:

```ini
[Unit]
Description=ИРУ v3.4 Server
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
systemctl status iru
```

---

## Управление пользователями

### Через админ-панель в UI (рекомендуется)

Войдите в UI под admin-токеном. Нажмите кнопку 👥 в правом верхнем углу:
- **Создание** — введите имя, нажмите «Создать»
- **Удаление** — кнопка × рядом с пользователем
- **Копирование токена** — клик по токену копирует в буфер

### Через API (curl)

**Создать пользователя:**
```bash
curl -X POST http://localhost:8000/api/admin/users \
  -H "X-Token: admin-токен" \
  -H "Content-Type: application/json" \
  -d '{"name": "Имя_тестера"}'
```

**Список пользователей:**
```bash
curl http://localhost:8000/api/admin/users \
  -H "X-Token: admin-токен"
```

**Удалить пользователя:**
```bash
curl -X DELETE http://localhost:8000/api/admin/users/2 \
  -H "X-Token: admin-токен"
```

---

## Как раздать тестеру

1. Создайте пользователя через админ-панель
2. Передайте тестеру:
   - Публичный URL сервера (для браузера)
   - Токен пользователя (для входа в UI)
   - Папку `agent/` (agent.py + config.json) или EXE-файл
3. Тестер редактирует `config.json`:
   - `device_id` — любое имя для своего ПК (латиница, без пробелов)
   - `server_url` — `ws://IP:8000` или `wss://домен`
   - `user_token` — его токен
4. Запускает `python agent.py` (или `agent.exe`)
5. Открывает URL сервера в браузере, вводит токен

Каждый тестер видит **только свои устройства** и **свои чаты**.

**Страница-инструкция:** `http://сервер:8000/instruction` — подробное руководство для тестера.

---

## Новое в v3.4

### Мобильная адаптация
- Адаптивный UI для смартфонов и планшетов
- Выдвижной сайдбар с оверлеем
- Гамбургер-меню в шапке
- Полноэкранные панели (проводник, админка) на мобильных

### Сбор данных для обучения
- Таблица `training_data` в SQLite
- Автоматическая запись: запрос → команды → результат
- Контекст системы: ОС, hostname, метод (powershell/bash)
- API для выгрузки данных: `GET /api/admin/training`

### Согласие на сбор данных
- Модальное окно при первом входе
- Данные собираются только с согласия пользователя
- Можно изменить через API: `POST /api/consent`

### Админ-панель в UI
- Создание и удаление пользователей без curl
- Копирование токена по клику
- Статистика пользователей

### Инструкция для тестера
- Standalone-страница: `/instruction`
- Пошаговое руководство на русском
- Тёмная тема в стиле ИРУ

---

## Функции UI

### Авторизация
- Экран входа по токену
- Автовход (токен сохраняется в localStorage)
- Кнопка «Выйти»

### Панель чатов (сайдбар)
- Список чатов с названиями (автогенерация из первого сообщения)
- Создание нового чата (+)
- Удаление чата (×)
- Переключение между чатами

### Память (контекст)
- Последние 50 сообщений чата загружаются в контекст LLM
- Скользящее окно: когда сообщений больше 50, старые отбрасываются
- Системный промт всегда присутствует

### Чат
- Ввод задачи на естественном языке
- Ответ ИРУ с компактным логом команд
- Сворачиваемые блоки с деталями выполнения
- Подсказки-чипы для быстрого старта
- Параллельное выполнение задач (v3.3)
- Broadcast — одна команда на все устройства (v3.3)

### Проводник
- Навигация по файловой системе устройства
- Открытие файлов/папок на устройстве
- Скачивание файлов через временные ссылки

### Мультиустройства
- Выбор устройства в выпадающем меню
- LLM знает все устройства и может маршрутизировать команды

---

## Структура файлов

```
iru_v3_new/
├── server/
│   ├── main.py           # FastAPI сервер + API эндпоинты + инструкция
│   ├── controller.py     # LLM-планировщик (DeepSeek)
│   ├── database.py       # SQLite: users, chats, messages, training_data
│   ├── llm_config.json   # Конфиг DeepSeek API
│   └── iru.db            # БД (создаётся при запуске)
├── agent/
│   ├── agent.py          # Агент-исполнитель
│   └── config.json       # Конфиг: device_id, server_url, user_token
├── ui/
│   └── index.html        # Веб-интерфейс (SPA, адаптивный)
├── requirements.txt      # Python-зависимости
└── README.md             # Документация
```

---

## API-эндпоинты

Все эндпоинты (кроме `/`, `/api/auth`, `/api/download/{token}`, `/instruction`) требуют заголовок `X-Token`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/` | UI (index.html) |
| GET | `/instruction` | Страница-инструкция для тестера |
| POST | `/api/auth` | Авторизация по токену |
| POST | `/api/consent` | Согласие на сбор данных |
| GET | `/api/devices` | Устройства пользователя |
| POST | `/api/chats` | Создать чат |
| GET | `/api/chats` | Список чатов |
| GET | `/api/chats/{id}/messages` | Сообщения чата |
| PATCH | `/api/chats/{id}` | Переименовать чат |
| DELETE | `/api/chats/{id}` | Удалить чат |
| POST | `/command` | Прямая команда агенту |
| POST | `/nl_command` | NL-команда через LLM |
| GET | `/api/tasks` | Список задач пользователя |
| GET | `/api/tasks/{task_id}` | Статус задачи |
| GET | `/api/download/{token}` | Скачать файл |
| POST | `/api/download_request` | Запрос на скачивание |
| GET | `/api/admin/users` | Список пользователей (admin) |
| POST | `/api/admin/users` | Создать пользователя (admin) |
| DELETE | `/api/admin/users/{id}` | Удалить пользователя (admin) |
| GET | `/api/admin/training` | Данные обучения (admin) |
| WS | `/ws/{device_id}?user_token=` | WebSocket для агентов |

---

## Дорожная карта

1. ✅ **Умный ассистент** — управление через NL, мультиустройства, параллельность
2. ✅ **Сбор данных** — автозапись training data с согласием
3. 🔜 **Обучение модели** — fine-tuning на собранных данных
4. 🔜 **Запуск** — публичный релиз

---

## Технологии

- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLite
- **LLM:** DeepSeek Chat (OpenAI-совместимый API)
- **Agent:** Python, websockets, subprocess
- **Frontend:** HTML/CSS/JS (без фреймворков), JetBrains Mono, адаптивный дизайн
- **Деплой:** VPS + Caddy (HTTPS) + systemd

"""
main.py — FastAPI-сервер ИРУ v3.4

Новое в v3.4:
  - Параллельное выполнение: задачи выполняются в фоне, UI не блокируется
  - Broadcast: одна команда на все/выбранные устройства одновременно
  - GET /api/tasks — список активных/завершённых задач
  - GET /api/tasks/{task_id} — статус конкретной задачи

Эндпоинты:
  GET  /                         — отдаёт index.html
  POST /api/auth                 — авторизация по токену
  GET  /api/devices              — устройства текущего пользователя
  POST /api/chats                — создать чат
  GET  /api/chats                — список чатов пользователя
  GET  /api/chats/{id}/messages  — сообщения чата
  DELETE /api/chats/{id}         — удалить чат
  PATCH /api/chats/{id}          — переименовать чат
  POST /command                  — прямая команда агенту
  POST /nl_command               — NL команда → фоновая задача (возвращает task_id)
  GET  /api/tasks                — список задач пользователя
  GET  /api/tasks/{task_id}      — статус задачи
  GET  /api/download/{token}     — скачать файл по временному токену
  POST /api/download_request     — запрос на скачивание (проводник)
  WS   /ws/{device_id}           — WebSocket для агентов

  Админ-эндпоинты:
  GET  /api/admin/users          — список пользователей
  POST /api/admin/users          — создать пользователя
  DELETE /api/admin/users/{id}   — удалить пользователя

Авторизация: заголовок X-Token или query-параметр ?token=
Агенты: query-параметр ?user_token= при подключении WS
"""

import json
import asyncio
import uuid
import base64
import time
from pathlib import Path
from contextlib import asynccontextmanager
from io import BytesIO

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from collections import defaultdict
from controller import process_nl_command

# ── RATE LIMITING ───────────────────────────────────────────
# Ограничение: макс 30 NL-команд в минуту на пользователя
RATE_LIMIT = 30  # команд
RATE_WINDOW = 60  # секунд
rate_counters: dict[str, list[float]] = defaultdict(list)

def check_rate_limit(user_id: str) -> bool:
    """Возвращает True если лимит НЕ превышен."""
    now = time.time()
    window_start = now - RATE_WINDOW
    # Очистить старые записи
    rate_counters[user_id] = [t for t in rate_counters[user_id] if t > window_start]
    if len(rate_counters[user_id]) >= RATE_LIMIT:
        return False
    rate_counters[user_id].append(now)
    return True

# ── ЧЁРНЫЙ СПИСОК ОПАСНЫХ КОМАНД ───────────────────────────
# Команды, которые агент НИКОГДА не должен выполнять
DANGEROUS_PATTERNS = [
    # Форматирование / удаление дисков
    r"format\s+[a-z]:", r"diskpart",
    # Рекурсивное удаление
    r"rm\s+-rf\s+/", r"rmdir\s+/s\s+/q\s+[a-z]:\\\\",
    r"del\s+/[sfq].*\\windows",
    # Реестр — удаление критических веток
    r"reg\s+delete\s+hklm",
    # Остановка критических сервисов
    r"net\s+stop\s+(windefend|mpssvc|wuauserv)",
    # Загрузка и выполнение из интернета
    r"powershell.*downloadstring", r"powershell.*downloadfile.*\|.*iex",
    r"certutil.*-urlcache.*-split",
    r"bitsadmin.*transfer",
    # Создание пользователей (эскалация)
    r"net\s+user\s+.*\s+/add", r"net\s+localgroup\s+administrators",
    # Отключение firewall
    r"netsh\s+advfirewall\s+set.*state\s+off",
    # Шифрование (ransomware pattern)
    r"cipher\s+/e",
]
import re as _re
_dangerous_re = [_re.compile(p, _re.IGNORECASE) for p in DANGEROUS_PATTERNS]

def is_command_safe(command: str) -> bool:
    """Проверяет команду на наличие опасных паттернов."""
    for pattern in _dangerous_re:
        if pattern.search(command):
            return False
    return True
from database import (
    init_db, get_user_by_token, create_user, list_users, delete_user,
    create_chat, list_chats, get_chat, update_chat_title, delete_chat,
    add_message, get_messages,
    add_training_record, get_training_data, get_training_count,
    set_user_consent,
)

# ── Хранение подключённых устройств ───────────────────────────────────────
devices: dict = {}

# ── Токены для скачивания файлов ─────────────────────────────────────────
download_tokens: dict = {}
TOKEN_TTL = 300  # 5 минут

# ── Очередь задач (in-memory) ────────────────────────────────────────────
# task_id -> {
#   "task_id": str,
#   "user_id": int,
#   "chat_id": int,
#   "message": str,
#   "device_ids": [str],       # на каких устройствах
#   "status": "running"|"done"|"error",
#   "results": {device_id: {...}},  # результаты по устройствам
#   "created_at": float,
# }
tasks: dict = {}
TASK_TTL = 3600  # хранить задачи 1 час


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[server] ИРУ v3.4 запущен")
    yield
    print("[server] ИРУ v3.4 остановлен")


app = FastAPI(title="ИРУ v3.4", lifespan=lifespan)

# Статические файлы (UI)
STATIC_DIR = Path(__file__).parent.parent / "ui"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Страница инструкции для тестера ──────────────────────────────────────
INSTRUCTION_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ИРУ — Инструкция для тестера</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0e17; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.7; padding: 40px 20px; }
.container { max-width: 700px; margin: 0 auto; }
h1 { color: #00d4ff; font-size: 24px; margin-bottom: 8px; }
.subtitle { color: #64748b; font-size: 12px; margin-bottom: 32px; }
h2 { color: #00d4ff; font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
h3 { color: #94a3b8; font-size: 14px; margin: 20px 0 8px; }
p { margin-bottom: 12px; color: #94a3b8; }
ol, ul { padding-left: 20px; margin-bottom: 16px; color: #94a3b8; }
li { margin-bottom: 8px; }
code { background: #141b2a; border: 1px solid #1e293b; border-radius: 4px; padding: 2px 6px; font-size: 13px; color: #00d4ff; }
pre { background: #0f1520; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; margin: 12px 0; overflow-x: auto; font-size: 12px; color: #e2e8f0; }
.warning { background: #1a1500; border: 1px solid #f59e0b33; border-radius: 8px; padding: 12px 16px; margin: 16px 0; color: #f59e0b; font-size: 12px; }
.step-num { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; background: #00d4ff20; border: 1px solid #00d4ff33; border-radius: 50%; font-size: 12px; color: #00d4ff; margin-right: 8px; }
a { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; }
a:hover { border-bottom-style: solid; }
.footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #1e293b; font-size: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.footer-brand { color: #94a3b8; }
.footer-link { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; font-size: 13px; }
.footer-link:hover { border-bottom-style: solid; }
</style>
</head>
<body>
<div class="container">
<h1>ИРУ — Инструкция для тестера</h1>
<div class="subtitle">Интеллектуальный Режим Управления v3.4</div>

<h2>Что такое ИРУ?</h2>
<p>ИРУ — система удалённого управления компьютером через естественный язык. Вы описываете задачу текстом, ИИ переводит её в команды и выполняет на вашем ПК.</p>

<h2>Что вам понадобится</h2>
<ul>
<li>Компьютер на <strong>Windows 10/11</strong></li>
<li>Установленный <strong>Python 3.10+</strong> (<a href="https://python.org/downloads" target="_blank">скачать</a>)</li>
<li><strong>Токен доступа</strong> — получите у администратора</li>
<li>Файлы агента: <code>agent.py</code> + <code>config.json</code></li>
</ul>

<h2>Шаг 1: Установите Python</h2>
<p>Скачайте Python с <a href="https://python.org/downloads" target="_blank">python.org</a>. При установке обязательно отметьте <code>Add Python to PATH</code>.</p>

<h2>Шаг 2: Установите зависимость</h2>
<p>Откройте терминал (Win+R → <code>cmd</code>) и выполните:</p>
<pre>pip install websockets</pre>

<h2>Шаг 3: Настройте агент</h2>
<p>Создайте папку (например, <code>C:\\IRU</code>) и поместите туда файлы <code>agent.py</code> и <code>config.json</code>.</p>
<p>Откройте <code>config.json</code> и заполните:</p>
<pre>{
  "device_id": "МОЙ_ПК",
  "server_url": "wss://irumode.ru",
  "user_token": "ваш-токен"
}</pre>
<ul>
<li><code>device_id</code> — любое имя для вашего ПК (латиница, без пробелов)</li>
<li><code>server_url</code> — адрес сервера (получите у администратора)</li>
<li><code>user_token</code> — ваш токен доступа</li>
</ul>

<h2>Шаг 4: Запустите агент</h2>
<p>В терминале перейдите в папку с агентом и запустите:</p>
<pre>cd C:\\IRU
python agent.py</pre>
<p>Или используйте готовый EXE-файл (если предоставлен):</p>
<pre>agent.exe</pre>
<p>Вы увидите сообщение о подключении:</p>
<pre>[agent] device=МОЙ_ПК, connecting to wss://irumode.ru...
[agent] connected</pre>

<h2>Шаг 5: Войдите в интерфейс</h2>
<ol>
<li>Откройте браузер и перейдите по адресу сервера</li>
<li>Введите ваш токен доступа</li>
<li>Выберите устройство в правом верхнем углу</li>
<li>Создайте новый чат и начните общение</li>
</ol>

<h2>Примеры команд</h2>
<ul>
<li><code>Открой браузер</code></li>
<li><code>Покажи IP-адрес</code></li>
<li><code>Сколько свободного места на диске?</code></li>
<li><code>Покажи запущенные процессы</code></li>
<li><code>Создай файл test.txt на рабочем столе с текстом "привет"</code></li>
<li><code>Какая версия Windows?</code></li>
</ul>

<div class="warning">
⚠️ <strong>Важно:</strong> Агент выполняет команды от имени вашего пользователя Windows. Не запрашивайте удаление системных файлов или форматирование дисков. ИРУ отклонит опасные команды, но будьте аккуратны.
</div>

<h2>Частые проблемы</h2>
<h3>Агент не подключается</h3>
<ul>
<li>Проверьте адрес сервера в <code>config.json</code></li>
<li>Убедитесь, что используете <code>wss://irumode.ru</code></li>
<li>Проверьте интернет-соединение</li>
</ul>

<h3>Устройство не появляется в UI</h3>
<ul>
<li>Убедитесь, что агент запущен и показывает <code>[agent] connected</code></li>
<li>Проверьте, что токен в <code>config.json</code> совпадает с вашим токеном для UI</li>
</ul>

<h3>Кракозябры в выводе</h3>
<p>ИРУ автоматически обрабатывает кодировку, но если проблема остаётся — укажите это в чате, ИРУ попробует другой подход.</p>

<div class="footer">
<span class="footer-brand">ИРУ v3.4 — Интеллектуальный Режим Управления</span>
<a href="/" class="footer-link">← Вернуться в ИРУ</a>
</div>
</div>
</body>
</html>"""


# ── Пользовательское соглашение и дисклеймер ────────────────────────────────────
TERMS_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ИРУ — Пользовательское соглашение</title>
<link rel="icon" type="image/x-icon" href="/static/IruIcon.ico">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0e17; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.7; padding: 40px 20px; }
.container { max-width: 700px; margin: 0 auto; }
h1 { color: #00d4ff; font-size: 24px; margin-bottom: 8px; }
.subtitle { color: #64748b; font-size: 12px; margin-bottom: 32px; }
h2 { color: #00d4ff; font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
p { margin-bottom: 12px; color: #94a3b8; }
ol, ul { padding-left: 20px; margin-bottom: 16px; color: #94a3b8; }
li { margin-bottom: 8px; }
.warning { background: #1a1500; border: 1px solid #f59e0b33; border-radius: 8px; padding: 12px 16px; margin: 16px 0; color: #f59e0b; font-size: 12px; }
a { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; }
a:hover { border-bottom-style: solid; }
.footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #1e293b; font-size: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.footer-brand { color: #94a3b8; }
.footer-link { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; font-size: 13px; }
.footer-link:hover { border-bottom-style: solid; }
</style>
</head>
<body>
<div class="container">
<h1>Пользовательское соглашение</h1>
<div class="subtitle">ИРУ — Интеллектуальный Режим Управления | Редакция от 16.04.2026</div>

<h2>1. Общие положения</h2>
<p>1.1. Настоящее Пользовательское соглашение (далее — «Соглашение») регулирует порядок использования системы ИРУ (далее — «Сервис»).</p>
<p>1.2. Используя Сервис, вы подтверждаете согласие с условиями настоящего Соглашения. Если вы не согласны, прекратите использование Сервиса.</p>
<p>1.3. Сервис находится на стадии бета-тестирования и предоставляется «как есть» (as is).</p>

<h2>2. Описание Сервиса</h2>
<p>2.1. ИРУ — система удалённого управления компьютером через естественный язык. Пользователь описывает задачу текстом, ИИ переводит её в команды и выполняет на устройстве.</p>
<p>2.2. Команды выполняются непосредственно на компьютере пользователя через программу-агент, установленную на устройстве.</p>

<h2>3. Права и обязанности пользователя</h2>
<p>3.1. Пользователь обязуется не использовать Сервис для:</p>
<ul>
<li>несанкционированного доступа к чужим устройствам или данным;</li>
<li>распространения вредоносного ПО, майнинга или иных противоправных действий;</li>
<li>попыток обхода систем защиты Сервиса;</li>
<li>создания чрезмерной нагрузки на сервер (флуд, DDoS).</li>
</ul>
<p>3.2. Пользователь несёт полную ответственность за команды, отправляемые через Сервис, и их последствия на своих устройствах.</p>
<p>3.3. Токен доступа является персональным. Передача токена третьим лицам запрещена.</p>

<h2>4. Сбор данных</h2>
<p>4.1. Сервис может собирать анонимизированные данные о взаимодействии с системой (текст запросов, выполненные команды, информация об ОС) исключительно для улучшения качества Сервиса и обучения модели.</p>
<p>4.2. Сбор данных осуществляется только при явном согласии пользователя (переключатель в интерфейсе). Пользователь может отозвать согласие в любой момент.</p>
<p>4.3. Персональные данные (имя пользователя, содержимое файлов) не передаются третьим лицам и не продаются.</p>

<h2>5. Дисклеймер</h2>
<div class="warning">
ВАЖНО: Прочитайте этот раздел полностью.
</div>
<p>5.1. Сервис выполняет команды непосредственно на вашем компьютере. Некорректная формулировка запроса или ошибка ИИ может привести к нежелательным последствиям, включая потерю данных.</p>
<p>5.2. Разработчик не несёт ответственности за:</p>
<ul>
<li>ущерб, причинённый выполнением команд, инициированных пользователем;</li>
<li>потерю данных, вызванную ошибками ИИ или сбоями оборудования;</li>
<li>перебои в работе Сервиса, вызванные техническими работами, обновлениями или форс-мажором;</li>
<li>действия третьих лиц, получивших доступ к токену пользователя.</li>
</ul>
<p>5.3. Несмотря на наличие системы блокировки опасных команд, она не гарантирует 100% защиту. Пользователь должен контролировать выполняемые команды и иметь резервные копии важных данных.</p>

<h2>6. Интеллектуальная собственность</h2>
<p>6.1. Все права на ПО, дизайн, код и документацию Сервиса принадлежат разработчику.</p>
<p>6.2. Использование Сервиса не даёт пользователю прав на исходный код, алгоритмы или технологии Сервиса.</p>

<h2>7. Ограничения Сервиса</h2>
<p>7.1. Разработчик вправе в любой момент ограничить или прекратить доступ пользователя к Сервису при нарушении условий Соглашения.</p>
<p>7.2. Сервис может включать ограничения на количество запросов, устройств и объём хранимых данных в зависимости от тарифного плана.</p>

<h2>8. Изменение условий</h2>
<p>8.1. Разработчик вправе изменять условия Соглашения. Актуальная версия всегда доступна по адресу <a href="/terms">/terms</a>.</p>
<p>8.2. Продолжение использования Сервиса после изменения условий означает согласие с новой редакцией.</p>

<h2>9. Контакты</h2>
<p>По всем вопросам: <a href="mailto:russaygushkin@gmail.com">russaygushkin@gmail.com</a></p>

<div class="footer">
<span class="footer-brand">ИРУ v3.4 — Интеллектуальный Режим Управления</span>
<a class="footer-link" href="/">Вернуться</a>
</div>
</div>
</body>
</html>"""


ABOUT_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ИРУ — О системе</title>
<link rel="icon" type="image/x-icon" href="/static/IruIcon.ico">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0e17; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 15px; line-height: 1.75; padding: 48px 20px; }
.container { max-width: 760px; margin: 0 auto; }
.page-header { margin-bottom: 40px; }
h1 { color: #00d4ff; font-size: 28px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 10px; }
.subtitle { color: #ffffff; font-size: 15px; font-weight: 400; opacity: 0.85; }
h2 { color: #00d4ff; font-size: 20px; font-weight: 600; margin: 36px 0 14px; padding-bottom: 8px; border-bottom: 1px solid #1e293b; }
p { margin-bottom: 14px; color: #ffffff; opacity: 0.9; }
strong { color: #ffffff; font-weight: 600; }
a { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; }
a:hover { border-bottom-style: solid; }

/* Philosophy block */
.philosophy { background: #0f1725; border: 1px solid #1e3a5f; border-left: 4px solid #00d4ff; border-radius: 0 10px 10px 0; padding: 18px 22px; margin: 18px 0 28px; }
.philosophy p { color: #ffffff; font-style: italic; margin: 0 0 10px; }
.philosophy p:last-child { margin: 0; }
.philosophy .no-emu { color: #e2e8f0; font-size: 13px; margin: 0; }

/* Architecture image */
.arch-img-wrap { margin: 18px 0 28px; }
.arch-img-wrap img { width: 100%; max-width: 100%; border-radius: 10px; border: 1px solid #1e3a5f; box-shadow: 0 8px 32px rgba(0,212,255,0.10), 0 2px 8px rgba(0,0,0,0.5); display: block; }

/* Roadmap stage cards */
.stages { display: flex; flex-direction: column; gap: 12px; margin: 18px 0 28px; }
.stage-card { background: #0f1725; border: 1px solid #1e293b; border-radius: 10px; padding: 16px 20px; display: flex; align-items: flex-start; gap: 16px; }
.stage-card.done { border-left: 3px solid #00d4ff; }
.stage-card.coming { border-left: 3px solid #f59e0b; }
.stage-badge { flex-shrink: 0; font-size: 13px; font-weight: 600; padding: 3px 10px; border-radius: 6px; white-space: nowrap; }
.stage-badge.done { background: #00d4ff18; border: 1px solid #00d4ff40; color: #00d4ff; }
.stage-badge.coming { background: #f59e0b18; border: 1px solid #f59e0b40; color: #f59e0b; }
.stage-content { flex: 1; }
.stage-title { color: #ffffff; font-weight: 600; font-size: 15px; margin-bottom: 2px; }
.stage-desc { color: #e2e8f0; font-size: 13px; opacity: 0.85; }

/* Tech stack table */
.tech-table { width: 100%; border-collapse: collapse; margin: 18px 0 28px; }
.tech-table tr { border-bottom: 1px solid #1e293b; }
.tech-table tr:last-child { border-bottom: none; }
.tech-table td { padding: 10px 14px; font-size: 14px; }
.tech-table td:first-child { color: #00d4ff; font-weight: 600; width: 42%; }
.tech-table td:last-child { color: #ffffff; }
.tech-table tr:nth-child(even) { background: #0f1725; }

/* Footer */
.footer { margin-top: 48px; padding-top: 18px; border-top: 1px solid #1e293b; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.footer-brand { color: #e2e8f0; font-size: 12px; }
.footer-link { color: #00d4ff; font-size: 13px; text-decoration: none; border-bottom: 1px dashed #00d4ff80; }
.footer-link:hover { border-bottom-style: solid; }
</style>
</head>
<body>
<div class="container">

<div class="page-header">
<h1>Об ИРУ — Интеллектуальный Режим Управления</h1>
<div class="subtitle">Система удалённого управления компьютерами через естественный язык</div>
</div>

<h2>Что такое ИРУ?</h2>
<p>ИРУ — система, в которой пользователь описывает задачу на естественном языке, языковая модель (LLM) переводит её в команды CMD/PowerShell, а агент выполняет их непосредственно на устройстве.</p>
<div class="philosophy">
<p>Философия: «научить машину быть машиной»</p>
<p class="no-emu">Никакой эмуляции действий пользователя — никаких скриншотов, кликов и pyautogui. Только прямые программные вызовы: COM-объекты, WMI, UI Automation API, DevTools Protocol. Машина управляется как машина.</p>
</div>

<h2>Архитектура системы</h2>
<div class="arch-img-wrap">
<img src="/static/architecture.jpg" alt="Архитектура ИРУ">
</div>

<h2>Этапы разработки</h2>
<div class="stages">

<div class="stage-card done">
<span class="stage-badge done">✅ Этап 1</span>
<div class="stage-content">
<div class="stage-title">Умный ассистент (Текст → Команды)</div>
<div class="stage-desc">NL-управление одним и несколькими устройствами одновременно, параллельное выполнение, broadcast-режим</div>
</div>
</div>

<div class="stage-card done">
<span class="stage-badge done">✅ Этап 2</span>
<div class="stage-content">
<div class="stage-title">Сбор данных (Обучающая выборка)</div>
<div class="stage-desc">Автоматическая запись training data с согласия пользователя для последующего обучения модели</div>
</div>
</div>

<div class="stage-card coming">
<span class="stage-badge coming">🔜 Этап 3</span>
<div class="stage-content">
<div class="stage-title">Обучение модели (Собственный ИИ)</div>
<div class="stage-desc">Fine-tuning собственной модели на реальных данных, собранных на этапе 2</div>
</div>
</div>

<div class="stage-card coming">
<span class="stage-badge coming">🔜 Этап 4</span>
<div class="stage-content">
<div class="stage-title">Запуск (Публичный релиз)</div>
<div class="stage-desc">Выход собственной модели и публичный релиз системы ИРУ</div>
</div>
</div>

</div>

<h2>Технологический стек</h2>
<table class="tech-table">
<tr><td>Сервер</td><td>FastAPI + Uvicorn</td></tr>
<tr><td>LLM</td><td>DeepSeek API</td></tr>
<tr><td>Агент</td><td>Python (WebSocket client)</td></tr>
<tr><td>Фронтенд</td><td>Vanilla JS, единый HTML-файл</td></tr>
<tr><td>База данных</td><td>SQLite</td></tr>
<tr><td>Протокол</td><td>WebSocket (двунаправленный, реального времени)</td></tr>
<tr><td>ОС агента</td><td>Windows (PowerShell / CMD)</td></tr>
</table>

<div class="footer">
<span class="footer-brand">ИРУ v3.4 — Интеллектуальный Режим Управления</span>
<a href="/" class="footer-link">← Вернуться в ИРУ</a>
</div>

</div>
</body>
</html>"""


# ── Модели запросов ────────────────────────────────────────────

class DirectCommand(BaseModel):
    device_id: str
    action: str
    params: dict = {}

class NLCommand(BaseModel):
    device_id: str
    message: str
    chat_id: int | None = None
    broadcast: bool = False  # отправить на все устройства
    device_ids: list[str] = []  # конкретные устройства (если broadcast=False)

class AuthRequest(BaseModel):
    token: str

class CreateChatRequest(BaseModel):
    title: str = ""

class RenameChatRequest(BaseModel):
    title: str

class CreateUserRequest(BaseModel):
    name: str


# ── Авторизация ──────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict:
    """Извлечь пользователя из заголовка X-Token или query ?token=."""
    token = request.headers.get("X-Token") or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется токен авторизации")
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return user


def get_user_devices(user_id: int) -> dict:
    """Получить устройства, принадлежащие пользователю."""
    return {did: dev for did, dev in devices.items() if dev.get("user_id") == user_id}


# ── Утилиты ──────────────────────────────────────────────────────────────

async def send_command_to_agent(device_id: str, action: str, params: dict) -> dict:
    """Отправить команду конкретному агенту и дождаться ответа."""
    # Проверка безопасности: блокируем опасные команды на самом низком уровне
    if action == "execute_cmd":
        cmd_text = params.get("command", "")
        if not is_command_safe(cmd_text):
            raise RuntimeError(f"Команда заблокирована системой безопасности: {cmd_text[:80]}")

    dev = devices.get(device_id)
    if not dev:
        raise RuntimeError(f"Устройство '{device_id}' не подключено")

    cmd_id = str(uuid.uuid4())[:8]
    future = asyncio.get_event_loop().create_future()
    dev["pending"][cmd_id] = future

    msg = json.dumps({
        "type": "command",
        "payload": {"id": cmd_id, "action": action, "params": params}
    })
    await dev["ws"].send_text(msg)

    try:
        result = await asyncio.wait_for(future, timeout=60.0)
    except asyncio.TimeoutError:
        dev["pending"].pop(cmd_id, None)
        raise RuntimeError("Таймаут ожидания ответа от агента")

    return result


def create_download_token(device_id: str, file_path: str) -> str:
    """Создать временный токен для скачивания файла."""
    token = str(uuid.uuid4())
    download_tokens[token] = {
        "device_id": device_id,
        "file_path": file_path,
        "created": time.time(),
    }
    now = time.time()
    expired = [t for t, v in download_tokens.items() if now - v["created"] > TOKEN_TTL]
    for t in expired:
        download_tokens.pop(t, None)
    return token


def get_file_link_fn(device_id: str, file_path: str) -> str:
    """Создать ссылку для скачивания файла (для LLM)."""
    token = create_download_token(device_id, file_path)
    return f"/api/download/{token}"


def cleanup_old_tasks():
    """Удалить задачи старше TASK_TTL."""
    now = time.time()
    expired = [tid for tid, t in tasks.items() if now - t["created_at"] > TASK_TTL]
    for tid in expired:
        tasks.pop(tid, None)


# ── Фоновое выполнение задачи ────────────────────────────────────────────

async def run_nl_task(task_id: str, user_id: int, message: str,
                      device_ids: list[str], chat_id: int):
    """
    Выполнить NL-задачу в фоне.
    Если одно устройство — стандартный LLM-цикл.
    Если несколько (broadcast) — LLM планирует на первом устройстве,
    затем команды повторяются на остальных.
    """
    task = tasks[task_id]
    is_broadcast = len(device_ids) > 1

    async def run_on_device(device_id: str):
        """Выполнить задачу на одном устройстве через LLM."""
        dev = devices.get(device_id)
        if not dev or dev.get("user_id") != user_id:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Устройство '{device_id}' не найдено или нет доступа",
                "commands": [],
            }

        device_info = dev.get("info", {})
        user_devs = get_user_devices(user_id)
        all_devices_info = {did: {"info": d.get("info", {})} for did, d in user_devs.items()}
        chat_history = get_messages(chat_id, limit=50)

        async def send_fn(target_device_id, action, params):
            target_dev = devices.get(target_device_id)
            if not target_dev or target_dev.get("user_id") != user_id:
                raise RuntimeError(f"Нет доступа к устройству '{target_device_id}'")
            return await send_command_to_agent(target_device_id, action, params)

        try:
            result = await process_nl_command(
                user_message=message,
                device_id=device_id,
                device_info=device_info,
                all_devices=all_devices_info,
                send_command_fn=send_fn,
                get_file_link_fn=get_file_link_fn,
                chat_history=chat_history,
            )
            return {
                "device_id": device_id,
                "status": "ok",
                "answer": result.get("answer", ""),
                "commands": result.get("commands", []),
            }
        except httpx.HTTPStatusError as e:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка LLM API: {e.response.status_code}",
                "commands": [],
            }
        except Exception as e:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка: {str(e)}",
                "commands": [],
            }

    async def replay_commands_on_device(device_id: str, commands: list):
        """Повторить готовые команды на устройстве (без LLM)."""
        dev = devices.get(device_id)
        if not dev or dev.get("user_id") != user_id:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Устройство '{device_id}' не найдено",
                "commands": [],
            }

        results = []
        for cmd in commands:
            cmd_text = cmd.get("command", "")
            if cmd_text.startswith("["):  # get_file_link и т.п. — пропустить
                continue
            try:
                result = await send_command_to_agent(
                    device_id, "execute_cmd",
                    {"command": cmd_text, "timeout": 30}
                )
                results.append({
                    "command": cmd_text,
                    "device_id": device_id,
                    "result": result,
                })
            except Exception as e:
                results.append({
                    "command": cmd_text,
                    "device_id": device_id,
                    "result": {"error": str(e)},
                })

        hostname = dev.get("info", {}).get("hostname", device_id)
        return {
            "device_id": device_id,
            "status": "ok",
            "answer": f"Команды выполнены на {hostname}",
            "commands": results,
        }

    try:
        if is_broadcast:
            # Broadcast: LLM планирует на первом устройстве
            primary_result = await run_on_device(device_ids[0])
            task["results"][device_ids[0]] = primary_result

            all_commands = primary_result.get("commands", [])
            answers = []

            dev0 = devices.get(device_ids[0])
            hostname0 = dev0["info"].get("hostname", device_ids[0]) if dev0 else device_ids[0]
            answers.append(f"[{hostname0}] {primary_result.get('answer', '')}")

            # Повторить команды на остальных устройствах
            if all_commands and len(device_ids) > 1:
                replay_coros = [
                    replay_commands_on_device(did, all_commands)
                    for did in device_ids[1:]
                ]
                replay_results = await asyncio.gather(*replay_coros, return_exceptions=True)

                for r in replay_results:
                    if isinstance(r, Exception):
                        answers.append(f"Ошибка: {str(r)}")
                    else:
                        task["results"][r["device_id"]] = r
                        if r.get("commands"):
                            all_commands.extend(r["commands"])
                        dev = devices.get(r["device_id"])
                        hostname = dev["info"].get("hostname", r["device_id"]) if dev else r["device_id"]
                        answers.append(f"[{hostname}] {r.get('answer', '')}")

            combined_answer = "\n\n".join(answers) if answers else "Готово."
            combined_commands = all_commands

        else:
            # Одно устройство: стандартная логика
            result = await run_on_device(device_ids[0])
            task["results"][device_ids[0]] = result
            combined_answer = result.get("answer", "")
            combined_commands = result.get("commands", [])

        # Сохранить ответ в чат
        add_message(chat_id, "assistant", combined_answer, combined_commands)

        # ── Training data: автозапись ──────────────────────────────────
        try:
            from database import get_db as _get_db, add_training_record
            with _get_db() as _conn:
                _row = _conn.execute("SELECT data_consent FROM users WHERE id = ?", (user_id,)).fetchone()
            if _row and _row["data_consent"]:
                first_dev = devices.get(device_ids[0]) if device_ids else None
                dev_info = first_dev.get("info", {}) if first_dev else {}
                os_info = dev_info.get("os", "")
                hostname_info = dev_info.get("hostname", "")
                method_info = "powershell" if "windows" in os_info.lower() else "bash"
                is_success = True
                add_training_record(
                    user_id=user_id, chat_id=chat_id,
                    input_text=message, os_info=os_info,
                    hostname=hostname_info, method=method_info,
                    running_processes=[], commands=combined_commands,
                    success=is_success,
                )
        except Exception as e:
            print(f"[training] Ошибка записи: {e}")

        task["status"] = "done"
        task["answer"] = combined_answer
        task["commands"] = combined_commands

    except Exception as e:
        task["status"] = "error"
        task["answer"] = f"Ошибка: {str(e)}"
        task["commands"] = []
        add_message(chat_id, "assistant", task["answer"])


# ── HTML ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "ui" / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ИРУ v3.4 — UI не найден</h1>")


@app.get("/instruction", response_class=HTMLResponse)
async def instruction_page():
    """Страница-инструкция для тестера."""
    return HTMLResponse(INSTRUCTION_HTML)


@app.get("/about", response_class=HTMLResponse)
async def about_page():
    """Страница «Об ИРУ»."""
    return HTMLResponse(ABOUT_HTML)


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    """Пользовательское соглашение и дисклеймер."""
    return HTMLResponse(TERMS_HTML)


# ── AUTH API ─────────────────────────────────────────────────────────────

@app.post("/api/auth")
async def auth(body: AuthRequest):
    """Авторизация по токену."""
    user = get_user_by_token(body.token)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "error": "Недействительный токен"}
        )
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "token": user["token"],
            "data_consent": bool(user.get("data_consent", 0)),
        },
    }


# ── CONSENT API ─────────────────────────────────────────────────

class ConsentRequest(BaseModel):
    consent: bool


@app.post("/api/consent")
async def api_set_consent(body: ConsentRequest, request: Request):
    """Установить согласие пользователя на сбор данных."""
    user = get_current_user(request)
    ok = set_user_consent(user["id"], body.consent)
    return {"status": "ok" if ok else "error"}


# ── ADMIN API ────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    users = list_users()
    # Маскируем токены: показываем только первые 8 символов
    for u in users:
        if u.get("token"):
            u["token"] = u["token"][:8] + "•" * 8
    return {"status": "ok", "users": users}


@app.post("/api/admin/users")
async def admin_create_user(body: CreateUserRequest, request: Request):
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    new_user = create_user(body.name)
    return {"status": "ok", "user": new_user}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    ok = delete_user(user_id)
    return {"status": "ok" if ok else "error", "deleted": ok}


@app.get("/api/admin/training")
async def admin_training_data(request: Request, limit: int = 100, offset: int = 0):
    """Записи обучения (только для администратора)."""
    user = get_current_user(request)
    if user["name"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    data = get_training_data(limit, offset)
    count = get_training_count()
    return {"status": "ok", "data": data, "total": count}


# ── DEVICES API ──────────────────────────────────────────────────────────

@app.get("/api/devices")
async def get_devices_api(request: Request):
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    result = {}
    for did, dev in user_devs.items():
        result[did] = {
            "device_id": did,
            "info": dev.get("info", {}),
            "connected": True,
        }
    return {"devices": result}


# ── CHATS API ────────────────────────────────────────────────────────────

@app.post("/api/chats")
async def api_create_chat(body: CreateChatRequest, request: Request):
    user = get_current_user(request)
    title = body.title.strip() or "Новый чат"
    chat = create_chat(user["id"], title)
    return {"status": "ok", "chat": chat}


@app.get("/api/chats")
async def api_list_chats(request: Request):
    user = get_current_user(request)
    chats = list_chats(user["id"])
    return {"status": "ok", "chats": chats}


@app.get("/api/chats/{chat_id}/messages")
async def api_get_messages(chat_id: int, request: Request):
    user = get_current_user(request)
    chat = get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    msgs = get_messages(chat_id, limit=50)
    return {"status": "ok", "messages": msgs}


@app.patch("/api/chats/{chat_id}")
async def api_rename_chat(chat_id: int, body: RenameChatRequest, request: Request):
    user = get_current_user(request)
    ok = update_chat_title(chat_id, user["id"], body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"status": "ok"}


@app.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: int, request: Request):
    user = get_current_user(request)
    ok = delete_chat(chat_id, user["id"])
    return {"status": "ok" if ok else "error", "deleted": ok}


# ── COMMAND API ──────────────────────────────────────────────────────────

@app.post("/command")
async def direct_command(cmd: DirectCommand, request: Request):
    """Прямая команда агенту (без LLM). Синхронная."""
    user = get_current_user(request)

    # Rate limiting
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    dev = devices.get(cmd.device_id)
    if not dev or dev.get("user_id") != user["id"]:
        return {"status": "error", "error": "Устройство не найдено или нет доступа"}
    try:
        result = await send_command_to_agent(cmd.device_id, cmd.action, cmd.params)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/nl_command")
async def nl_command(cmd: NLCommand, request: Request):
    """
    NL-команда → фоновая задача.
    Возвращает task_id сразу, не дожидаясь выполнения.
    Поддерживает broadcast (на все устройства) и список device_ids.
    """
    user = get_current_user(request)
    cleanup_old_tasks()

    # Rate limiting
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    # Определить целевые устройства
    user_devs = get_user_devices(user["id"])

    if cmd.broadcast:
        # Все устройства пользователя
        target_ids = list(user_devs.keys())
    elif cmd.device_ids:
        # Конкретные устройства
        target_ids = [did for did in cmd.device_ids if did in user_devs]
    else:
        # Одно устройство (как раньше)
        if cmd.device_id not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено или нет доступа"}
        target_ids = [cmd.device_id]

    if not target_ids:
        return {"status": "error", "error": "Нет доступных устройств"}

    # Создать/определить чат
    chat_id = cmd.chat_id
    if not chat_id:
        title = cmd.message[:50].strip() or "Новый чат"
        chat = create_chat(user["id"], title)
        chat_id = chat["id"]
    else:
        chat = get_chat(chat_id, user["id"])
        if not chat:
            return {"status": "error", "error": "Чат не найден"}

    # Сохранить сообщение пользователя
    add_message(chat_id, "user", cmd.message)

    # Создать задачу
    task_id = str(uuid.uuid4())[:12]
    tasks[task_id] = {
        "task_id": task_id,
        "user_id": user["id"],
        "chat_id": chat_id,
        "message": cmd.message,
        "device_ids": target_ids,
        "status": "running",
        "results": {},
        "answer": None,
        "commands": None,
        "created_at": time.time(),
    }

    # Запустить в фоне
    asyncio.create_task(run_nl_task(task_id, user["id"], cmd.message, target_ids, chat_id))

    return {
        "status": "ok",
        "task_id": task_id,
        "chat_id": chat_id,
        "device_ids": target_ids,
    }


# ── TASKS API ────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def api_list_tasks(request: Request):
    """Список задач текущего пользователя."""
    user = get_current_user(request)
    cleanup_old_tasks()
    user_tasks = [t for t in tasks.values() if t["user_id"] == user["id"]]
    user_tasks.sort(key=lambda t: t["created_at"], reverse=True)
    return {
        "status": "ok",
        "tasks": [{
            "task_id": t["task_id"],
            "chat_id": t["chat_id"],
            "message": t["message"][:80],
            "device_ids": t["device_ids"],
            "status": t["status"],
            "answer": t.get("answer"),
            "commands": t.get("commands"),
            "created_at": t["created_at"],
        } for t in user_tasks[:20]],
    }


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str, request: Request):
    """Статус конкретной задачи."""
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {
        "status": "ok",
        "task": {
            "task_id": task["task_id"],
            "chat_id": task["chat_id"],
            "message": task["message"],
            "device_ids": task["device_ids"],
            "status": task["status"],
            "answer": task.get("answer"),
            "commands": task.get("commands"),
            "results": task.get("results", {}),
            "created_at": task["created_at"],
        }
    }


# ── DOWNLOAD API ─────────────────────────────────────────────────────────

@app.get("/api/download/{token}")
async def download_file(token: str):
    info = download_tokens.pop(token, None)
    if not info:
        return {"status": "error", "error": "Ссылка недействительна или истекла"}

    if time.time() - info["created"] > TOKEN_TTL:
        return {"status": "error", "error": "Ссылка истекла"}

    device_id = info["device_id"]
    file_path = info["file_path"]

    try:
        result = await send_command_to_agent(
            device_id, "get_file_content", {"path": file_path}
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if "error" in result and result["error"]:
        return {"status": "error", "error": result["error"]}

    data = base64.b64decode(result["data_b64"])
    filename = result.get("filename", "file")

    return StreamingResponse(
        BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/download_request")
async def download_request(body: dict, request: Request):
    user = get_current_user(request)
    device_id = body.get("device_id")
    file_path = body.get("file_path")
    if not device_id or not file_path:
        return {"status": "error", "error": "device_id и file_path обязательны"}

    dev = devices.get(device_id)
    if not dev or dev.get("user_id") != user["id"]:
        return {"status": "error", "error": "Нет доступа к устройству"}

    token = create_download_token(device_id, file_path)
    return {"status": "ok", "url": f"/api/download/{token}"}


# ── WebSocket для агентов ────────────────────────────────────────────────

@app.websocket("/ws/{device_id}")
async def websocket_agent(ws: WebSocket, device_id: str, user_token: str = Query(default="")):
    user = get_user_by_token(user_token) if user_token else None
    if not user:
        await ws.close(code=4001, reason="Недействительный токен пользователя")
        return

    await ws.accept()
    print(f"[ws] agent connected: {device_id} (user: {user['name']})")

    devices[device_id] = {
        "ws": ws,
        "info": {},
        "pending": {},
        "user_id": user["id"],
    }

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "register":
                devices[device_id]["info"] = data.get("payload", {})
                print(f"[ws] registered: {device_id} — {data.get('payload', {})}")

            elif msg_type == "result":
                payload = data.get("payload", {})
                cmd_id = payload.get("id")
                future = devices[device_id]["pending"].pop(cmd_id, None)
                if future and not future.done():
                    if payload.get("status") == "ok":
                        future.set_result(payload.get("result", {}))
                    else:
                        future.set_result({"error": payload.get("error", "Неизвестная ошибка")})

    except WebSocketDisconnect:
        print(f"[ws] agent disconnected: {device_id}")
    except Exception as e:
        print(f"[ws] error for {device_id}: {e}")
    finally:
        devices.pop(device_id, None)


# ── Запуск ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

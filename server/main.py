"""
main.py — FastAPI-сервер ИРУ v3.5

Новое в v3.5:
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
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from collections import defaultdict
from controller import process_nl_command, process_onboarding_message, ConfirmationRequired

# ── ADMIN CHECK ─────────────────────────────────────────────
ADMIN_USER_ID = 1  # ID admin-пользователя (создаётся первым в init_db)

def _is_admin(user: dict) -> bool:
    """Проверка админа по ID, не по имени."""
    return user.get("id") == ADMIN_USER_ID


def _dk(user_id: int, device_id: str) -> str:
    """Составной ключ для devices dict: изоляция по пользователю."""
    return f"{user_id}:{device_id}"


def _short_did(composite_key: str) -> str:
    """Извлечь короткий device_id из составного ключа."""
    return composite_key.split(":", 1)[1] if ":" in composite_key else composite_key




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

# Ограничение по IP: 10 запросов/мин (защита от спама вкладками)
IP_RATE_LIMIT = 10
IP_RATE_WINDOW = 60
ip_rate_counters: dict[str, list[float]] = defaultdict(list)

def check_ip_rate_limit(ip: str) -> bool:
    now = time.time()
    window_start = now - IP_RATE_WINDOW
    ip_rate_counters[ip] = [t for t in ip_rate_counters[ip] if t > window_start]
    if len(ip_rate_counters[ip]) >= IP_RATE_LIMIT:
        return False
    ip_rate_counters[ip].append(now)
    return True

# ── ЧЁРНЫЙ СПИСОК ОПАСНЫХ КОМАНД ───────────────────────────
# Команды, которые агент НИКОГДА не должен выполнять
# Команды, которые ЗАПРЕЩЕНЫ полностью (никогда не выполняются)
DANGEROUS_PATTERNS = [
    # Форматирование / удаление дисков
    r"format\s+[a-z]:", r"diskpart",
    # Рекурсивное удаление системных папок
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

# Команды, которые требуют подтверждения пользователя
CONFIRM_PATTERNS = [
    r"remove-item",
    r"del\s+",
    r"rd\s+",
    r"rmdir\s+",
    r"stop-process",
    r"kill\s+",
    r"taskkill",
    r"shutdown",
    r"restart-computer",
    r"move-item.*-force",
    r"clear-content",
    r"set-content",       # перезапись файла
    r"out-file",          # перезапись файла
    r"uninstall",
]

import re as _re
_dangerous_re = [_re.compile(p, _re.IGNORECASE) for p in DANGEROUS_PATTERNS]
_confirm_re = [_re.compile(p, _re.IGNORECASE) for p in CONFIRM_PATTERNS]

def is_command_safe(command: str) -> bool:
    """Проверяет команду на наличие ЗАПРЕЩЁННЫХ паттернов."""
    for pattern in _dangerous_re:
        if pattern.search(command):
            return False
    return True

def needs_confirmation(command: str) -> bool:
    """Проверяет, требует ли команда подтверждения пользователя."""
    for pattern in _confirm_re:
        if pattern.search(command):
            return True
    return False
from database import (
    init_db, get_user_by_token, get_user_by_id, create_user, list_users, delete_user,
    create_chat, list_chats, get_chat, update_chat_title, delete_chat,
    add_message, get_messages,
    add_training_record, get_training_data, get_training_count,
    set_user_consent,
    PLAN_LIMITS, get_user_plan, set_user_plan,
    check_daily_command_limit, increment_daily_commands,
    check_device_limit, accept_terms, has_accepted_terms,
    store_refresh_token, get_refresh_token, revoke_refresh_token,
    revoke_all_refresh_tokens, cleanup_expired_refresh_tokens,
    add_audit_log, get_audit_log, get_audit_log_count,
    upsert_device_profile, get_device_profile, get_user_device_profiles,
)
from auth import (
    create_access_token, verify_access_token, create_refresh_token,
    is_jwt, ACCESS_TTL, REFRESH_TTL,
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

async def _cleanup_tokens_loop():
    """Periodically clean up expired refresh tokens."""
    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            cleanup_expired_refresh_tokens()
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_expired_refresh_tokens()  # clean on startup
    task = asyncio.create_task(_cleanup_tokens_loop())
    print("[server] ИРУ v3.5 запущен")
    yield
    task.cancel()
    print("[server] ИРУ v3.5 остановлен")


app = FastAPI(title="ИРУ v3.5", lifespan=lifespan)

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
<link rel="icon" type="image/x-icon" href="/static/IruIcon.ico">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #020308; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.7; padding: 40px 20px; }
.container { max-width: 700px; margin: 0 auto; }
h1 { color: #00d4ff; font-size: 24px; margin-bottom: 8px; }
.subtitle { color: #64748b; font-size: 12px; margin-bottom: 32px; }
h2 { color: #00d4ff; font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
h3 { color: #94a3b8; font-size: 14px; margin: 20px 0 8px; }
p { margin-bottom: 12px; color: #94a3b8; }
ol, ul { padding-left: 20px; margin-bottom: 16px; color: #94a3b8; }
li { margin-bottom: 8px; }
code { background: #0a0e17; border: 1px solid #1e293b; border-radius: 4px; padding: 2px 6px; font-size: 13px; color: #00d4ff; }
pre { background: #0a0e17; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; margin: 12px 0; overflow-x: auto; font-size: 12px; color: #e2e8f0; }
.warning { background: #1a1500; border: 1px solid #f59e0b33; border-radius: 8px; padding: 12px 16px; margin: 16px 0; color: #f59e0b; font-size: 12px; }
.info { background: #001a2e; border: 1px solid #00d4ff33; border-radius: 8px; padding: 12px 16px; margin: 16px 0; color: #00d4ff; font-size: 12px; }
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
<div class="subtitle">Интеллектуальный Режим Управления v3.5</div>

<h2>Что такое ИРУ?</h2>
<p>ИРУ — система удалённого управления компьютером через естественный язык. Вы описываете задачу текстом, ИИ переводит её в команды и выполняет на вашем ПК.</p>

<h2>Что вам понадобится</h2>
<ul>
<li>Компьютер на <strong>Windows 10/11</strong></li>
<li><strong>Токен доступа</strong> — получите у администратора</li>
<li>Файл <code>agent.exe</code></li>
</ul>

<h2>Шаг 1: Скачайте agent.exe</h2>
<p>Получите файл <code>agent.exe</code> от администратора. Установка не требуется.</p>

<h2>Шаг 2: Запустите agent.exe</h2>
<p>Двойной клик по <code>agent.exe</code>. При первом запуске откроется окно ввода токена.</p>

<div class="info">
Windows SmartScreen может показать предупреждение при первом запуске. Нажмите <strong>«Подробнее»</strong>, затем <strong>«Выполнить в любом случае»</strong>. Это стандартное поведение для файлов, загруженных из интернета.
</div>

<p>Вставьте токен доступа и нажмите <strong>«Подключиться»</strong>. Токен сохранится автоматически — при следующих запусках вводить не нужно.</p>
<p>Вы увидите окно консоли с сообщением о подключении:</p>
<pre>[agent] connected</pre>

<h2>Шаг 3: Войдите в интерфейс</h2>
<ol>
<li>Откройте браузер: <a href="https://irumode.ru" target="_blank">irumode.ru</a></li>
<li>Введите тот же токен доступа</li>
<li>Ваше устройство появится автоматически</li>
<li>Создайте чат и начните общение</li>
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
Важно: Агент выполняет команды от имени вашего пользователя Windows. Не запрашивайте удаление системных файлов или форматирование дисков. ИРУ отклонит опасные команды, но будьте аккуратны.
</div>

<h2>Тарифные планы</h2>
<p>Во время бета-тестирования все участники получают план <strong>Pro</strong> бесплатно.</p>
<ul>
<li><strong>Free</strong> — 1 устройство, 30 команд/день</li>
<li><strong>Pro</strong> — безлимитные устройства и команды, файловый проводник, DEV MODE</li>
<li><strong>Business</strong> — мультипользовательская админка, аудит-логи, API</li>
</ul>

<h2>Частые проблемы</h2>
<h3>Агент не подключается</h3>
<ul>
<li>Проверьте интернет-соединение</li>
<li>Убедитесь, что токен введён правильно</li>
<li>Попробуйте удалить <code>config.json</code> рядом с exe и запустить заново — окно ввода токена появится снова</li>
</ul>

<h3>SmartScreen блокирует запуск</h3>
<p>Нажмите «Подробнее» и «Выполнить в любом случае». Файл безопасен, предупреждение связано с отсутствием цифровой подписи.</p>

<h3>Устройство не появляется в UI</h3>
<ul>
<li>Убедитесь, что агент запущен и показывает <code>[agent] connected</code></li>
<li>Убедитесь, что в UI вы вошли с тем же токеном</li>
</ul>

<h3>Кракозябры в выводе</h3>
<p>ИРУ автоматически обрабатывает кодировку (UTF-8), но если проблема остаётся — укажите это в чате, ИРУ попробует другой подход.</p>

<div class="footer">
<span class="footer-brand">ИРУ v3.5 — Интеллектуальный Режим Управления</span>
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
body { background: #020308; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.7; padding: 40px 20px; }
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
<div class="subtitle">ИРУ — Интеллектуальный Режим Управления | Редакция от 18.04.2026</div>

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
<span class="footer-brand">ИРУ v3.5 — Интеллектуальный Режим Управления</span>
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
body { background: #020308; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 15px; line-height: 1.75; padding: 48px 20px; }
.container { max-width: 760px; margin: 0 auto; }
.page-header { margin-bottom: 40px; }
h1 { color: #00d4ff; font-size: 28px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 10px; }
.subtitle { color: #ffffff; font-size: 15px; font-weight: 400; opacity: 0.85; }
h2 { color: #00d4ff; font-size: 20px; font-weight: 600; margin: 36px 0 14px; padding-bottom: 8px; border-bottom: 1px solid #1e293b; }
p { margin-bottom: 14px; color: #ffffff; opacity: 0.9; }
strong { color: #ffffff; font-weight: 600; }
ul { padding-left: 20px; margin-bottom: 16px; color: #ffffff; opacity: 0.9; }
li { margin-bottom: 8px; }
a { color: #00d4ff; text-decoration: none; border-bottom: 1px dashed #00d4ff80; }
a:hover { border-bottom-style: solid; }

/* Philosophy block */
.philosophy { background: #0a0e17; border: 1px solid #1e3a5f; border-left: 4px solid #00d4ff; border-radius: 0 10px 10px 0; padding: 18px 22px; margin: 18px 0 28px; }
.philosophy p { color: #ffffff; font-style: italic; margin: 0 0 10px; }
.philosophy p:last-child { margin: 0; }
.philosophy .no-emu { color: #e2e8f0; font-size: 13px; margin: 0; font-style: normal; }

/* Architecture image */
.arch-img-wrap { margin: 18px 0 28px; }
.arch-img-wrap img { width: 100%; max-width: 100%; border-radius: 10px; border: 1px solid #1e3a5f; box-shadow: 0 8px 32px rgba(0,212,255,0.10), 0 2px 8px rgba(0,0,0,0.5); display: block; }

/* Roadmap stage cards */
.stages { display: flex; flex-direction: column; gap: 12px; margin: 18px 0 28px; }
.stage-card { background: #0a0e17; border: 1px solid #1e293b; border-radius: 10px; padding: 16px 20px; display: flex; align-items: flex-start; gap: 16px; }
.stage-card.done { border-left: 3px solid #00d4ff; }
.stage-card.coming { border-left: 3px solid #f59e0b; }
.stage-badge { flex-shrink: 0; font-size: 13px; font-weight: 600; padding: 3px 10px; border-radius: 6px; white-space: nowrap; }
.stage-badge.done { background: #00d4ff18; border: 1px solid #00d4ff40; color: #00d4ff; }
.stage-badge.coming { background: #f59e0b18; border: 1px solid #f59e0b40; color: #f59e0b; }
.stage-content { flex: 1; }
.stage-title { color: #ffffff; font-weight: 600; font-size: 15px; margin-bottom: 2px; }
.stage-desc { color: #e2e8f0; font-size: 13px; opacity: 0.85; }

/* Plan cards */
.plans { display: flex; flex-direction: column; gap: 10px; margin: 18px 0 28px; }
.plan-card { background: #0a0e17; border: 1px solid #1e293b; border-radius: 10px; padding: 14px 20px; display: flex; align-items: center; gap: 16px; }
.plan-card.active { border-color: #00d4ff40; }
.plan-name { color: #00d4ff; font-weight: 600; font-size: 15px; min-width: 80px; }
.plan-desc { color: #e2e8f0; font-size: 13px; opacity: 0.85; }
.plan-beta { font-size: 11px; color: #f59e0b; background: #f59e0b18; border: 1px solid #f59e0b40; padding: 2px 8px; border-radius: 4px; margin-left: auto; white-space: nowrap; }

/* Tech stack table */
.tech-table { width: 100%; border-collapse: collapse; margin: 18px 0 28px; }
.tech-table tr { border-bottom: 1px solid #1e293b; }
.tech-table tr:last-child { border-bottom: none; }
.tech-table td { padding: 10px 14px; font-size: 14px; }
.tech-table td:first-child { color: #00d4ff; font-weight: 600; width: 42%; }
.tech-table td:last-child { color: #ffffff; }
.tech-table tr:nth-child(even) { background: #0a0e17; }

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
<p class="no-emu">Никакой эмуляции действий пользователя. Никаких скриншотов, кликов или pyautogui. Только прямые программные вызовы: COM-объекты, WMI, UI Automation API, DevTools Protocol. Машина управляется как машина.</p>
</div>

<h2>Архитектура системы</h2>
<div class="arch-img-wrap">
<img src="/static/architecture.jpg" alt="Архитектура ИРУ">
</div>

<h2>Тарифные планы</h2>
<p>Во время бета-тестирования все участники получают план Pro бесплатно.</p>
<div class="plans">
<div class="plan-card">
<span class="plan-name">Free</span>
<span class="plan-desc">1 устройство, 30 команд/день, 7 дней история</span>
</div>
<div class="plan-card active">
<span class="plan-name">Pro</span>
<span class="plan-desc">Безлимитные устройства и команды, проводник, DEV MODE</span>
<span class="plan-beta">БЕТА</span>
</div>
<div class="plan-card">
<span class="plan-name">Business</span>
<span class="plan-desc">Мультипользовательская админка, аудит-логи, API, приоритетная поддержка</span>
</div>
</div>

<h2>DEV MODE</h2>
<p>Прямой доступ к командной строке устройства без участия LLM. Команды отправляются напрямую в PowerShell/CMD агента. Доступен на плане Pro и выше.</p>
<ul>
<li>Аккордеон-вывод с группировкой по устройствам</li>
<li>Автоматическая UTF-8 кодировка вывода</li>
<li>Кнопка рядом с проводником в интерфейсе</li>
<li>Чат не сохраняется на сервере</li>
</ul>

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
<tr><td>LLM</td><td>DeepSeek API (deepseek-chat)</td></tr>
<tr><td>Агент</td><td>Python (WebSocket client, .exe)</td></tr>
<tr><td>Фронтенд</td><td>Vanilla JS + HTML + CSS</td></tr>
<tr><td>База данных</td><td>SQLite</td></tr>
<tr><td>Протокол</td><td>WebSocket (двунаправленный, реальное время)</td></tr>
<tr><td>ОС агента</td><td>Windows 10/11 (PowerShell / CMD)</td></tr>
<tr><td>Хостинг</td><td>VPS + Caddy (HTTPS)</td></tr>
</table>

<h2>Контакты</h2>
<p>Сайт: <a href="https://irumode.online" target="_blank">irumode.online</a></p>
<p>Почта: <a href="mailto:russaygushkin@gmail.com">russaygushkin@gmail.com</a></p>

<div class="footer">
<span class="footer-brand">ИРУ v3.5 — Интеллектуальный Режим Управления</span>
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
    device_id: str = ""  # пустая строка допустима (онбординг без устройств)
    message: str
    chat_id: int | None = None
    broadcast: bool = False  # отправить на все устройства
    device_ids: list[str] = []  # конкретные устройства (если broadcast=False)
    modes: dict = {}  # флаги режимов: {pipeline: bool, autonomous: bool}

class AuthRequest(BaseModel):
    token: str

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class CreateChatRequest(BaseModel):
    title: str = ""

class RenameChatRequest(BaseModel):
    title: str

class CreateUserRequest(BaseModel):
    name: str


# ── Авторизация ──────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict:
    """
    Извлечь пользователя из:
      1. Authorization: Bearer <jwt>  (новый формат)
      2. X-Token: <jwt_or_uuid>       (совместимость)
      3. ?token=<uuid>                 (query, только старый формат)
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.headers.get("X-Token") or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется токен авторизации")

    # JWT access token
    if is_jwt(token):
        payload = verify_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Токен истёк или недействителен")
        user = get_user_by_id(int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        return user

    # Старый статичный UUID-токен (обратная совместимость)
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return user


def get_user_devices(user_id: int) -> dict:
    """Получить устройства, принадлежащие пользователю."""
    return {did: dev for did, dev in devices.items() if dev.get("user_id") == user_id}


# ── Утилиты ──────────────────────────────────────────────────────────────


async def send_command_to_agent(device_id: str, action: str, params: dict,
                                user_id: int | None = None,
                                skip_confirm: bool = False) -> dict:
    """Отправить команду конкретному агенту и дождаться ответа."""
    if action == "execute_cmd":
        cmd_text = params.get("command", "")
        # Лимит длины команды
        if len(cmd_text) > 2000:
            raise RuntimeError(
                "Команда слишком длинная (>2000 символов). "
                "Используй write_content для создания текстовых файлов, "
                "а не PowerShell-строки."
            )
        # Запрет Word COM для текста
        low = cmd_text.lower()
        if "word.application" in low and ("typetext" in low or "typeparagraph" in low):
            raise RuntimeError(
                "Создание текстовых файлов через Word.Application/TypeText запрещено. "
                "Используй инструмент write_content — он создаёт файл напрямую и безопасно."
            )
        # Запрет Invoke-WebRequest к поисковикам
        if "invoke-webrequest" in low or "iwr " in low or "curl " in low or "wget " in low:
            search_hosts = ("duckduckgo.com", "google.com/search", "bing.com/search", "yandex.ru/search")
            if any(h in low for h in search_hosts):
                raise RuntimeError(
                    "Поиск в интернете через Invoke-WebRequest/curl/wget запрещён. "
                    "Используй инструмент web_search."
                )
        # ЗАПРЕЩЕНО полностью (всегда блокируем)
        if not is_command_safe(cmd_text):
            raise RuntimeError(
                f"BLOCKED: Команда запрещена на этапе бета-тестирования. "
                f"Сообщи пользователю, что эта команда недоступна в бета-версии."
            )
        # ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ (можно пропустить после подтверждения)
        if not skip_confirm and needs_confirmation(cmd_text):
            print(f"[security] CONFIRM_REQUIRED: {cmd_text[:80]}")
            raise RuntimeError(
                f"CONFIRM_REQUIRED: Команда требует подтверждения пользователя."
            )
        print(f"[cmd] executing: {cmd_text[:80]}")

    elif action == "write_content":
        # Запрет записи в критические системные пути на обеих ОС
        path = str(params.get("path", "")).strip()
        if not path:
            raise RuntimeError("BLOCKED: путь не указан")
        # Нормализуем слеши для проверки
        norm = path.replace("\\", "/").lower()
        forbidden_prefixes = (
            # Windows
            "c:/windows/", "c:/program files/", "c:/program files (x86)/",
            "c:/programdata/", "c:/system volume information/",
            # Linux
            "/etc/", "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/usr/lib/",
            "/boot/", "/dev/", "/proc/", "/sys/", "/var/log/", "/root/",
        )
        if any(norm.startswith(p) for p in forbidden_prefixes):
            raise RuntimeError(
                f"BLOCKED: Запись в системные каталоги запрещена на этапе бета-тестирования: {path}"
            )
        content_len = len(str(params.get("content", "")))
        mode = "append" if params.get("append") else "write"
        print(f"[cmd] write_content ({mode}): {path} | {content_len} chars")

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


def create_download_token(device_id: str, file_path: str, user_id: int = 0) -> str:
    """Создать временный токен для скачивания файла."""
    token = str(uuid.uuid4())
    download_tokens[token] = {
        "device_id": device_id,
        "file_path": file_path,
        "user_id": user_id,
        "created": time.time(),
    }
    now = time.time()
    expired = [t for t, v in download_tokens.items() if now - v["created"] > TOKEN_TTL]
    for t in expired:
        download_tokens.pop(t, None)
    return token


def get_file_link_fn(device_id: str, file_path: str, user_id: int = 0) -> str:
    """Создать ссылку для скачивания файла (для LLM)."""
    token = create_download_token(device_id, file_path, user_id=user_id)
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
    print(f"[run_nl_task] START task={task_id[:8]}, user={user_id}, devices={device_ids}")

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
        # Получить сохранённый профиль устройства из БД (по короткому device_id)
        device_profile = get_device_profile(_short_did(device_id))

        # Автономный режим: пропускаем подтверждение опасных команд (BLOCKED остаётся)
        task_modes = task.get("modes") or {}
        autonomous_flag = bool(task_modes.get("autonomous"))

        async def send_fn(target_device_id, action, params):
            # LLM передаёт короткий device_id — конвертируем в составной ключ
            target_dk = _dk(user_id, target_device_id) if ":" not in target_device_id else target_device_id
            target_dev = devices.get(target_dk)
            if not target_dev or target_dev.get("user_id") != user_id:
                raise RuntimeError(f"Нет доступа к устройству '{target_device_id}'")
            return await send_command_to_agent(
                target_dk, action, params,
                user_id=user_id, skip_confirm=autonomous_flag,
            )

        # Замыкание для get_file_link_fn с user_id контекстом
        def _file_link_fn(dev_id: str, fpath: str) -> str:
            return get_file_link_fn(dev_id, fpath, user_id=user_id)

        try:
            result = await process_nl_command(
                user_message=message,
                device_id=_short_did(device_id),
                device_info=device_info,
                all_devices={_short_did(k): v for k, v in all_devices_info.items()},
                send_command_fn=send_fn,
                get_file_link_fn=_file_link_fn,
                chat_history=chat_history,
                device_profile=device_profile,
                modes=task_modes,
                user_id=user_id,
                chat_id=chat_id,
            )
            return {
                "device_id": device_id,
                "status": "ok",
                "answer": result.get("answer", ""),
                "commands": result.get("commands", []),
                "tasks": result.get("tasks", []),
            }
        except ConfirmationRequired as cr:
            return {
                "device_id": device_id,
                "status": "confirm",
                "answer": cr.answer,
                "commands": cr.commands_log,
                "confirm_data": {
                    "command": cr.command,
                    "device_id": cr.device_id,
                    "params": cr.params,
                },
            }
        except httpx.HTTPStatusError as e:
            print(f"[run_nl_task] LLM HTTP error on device={device_id}: {e.response.status_code}")
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка LLM API: {e.response.status_code}",
                "commands": [],
            }
        except Exception as e:
            import traceback
            print(f"[run_nl_task] ERROR on device={device_id}: {type(e).__name__}: {e}")
            traceback.print_exc()
            err_text = str(e) or type(e).__name__
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка: {err_text}",
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
                    {"command": cmd_text, "timeout": 30},
                    user_id=user_id,
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

            # Проверка: команда требует подтверждения
            if result.get("status") == "confirm":
                task["status"] = "confirm"
                task["answer"] = result.get("answer", "")
                task["commands"] = result.get("commands", [])
                task["confirm_data"] = result.get("confirm_data", {})
                task["confirm_data"]["chat_id"] = chat_id
                task["confirm_data"]["user_id"] = user_id
                return  # ждём подтверждения от UI

            combined_answer = result.get("answer", "")
            combined_commands = result.get("commands", [])

        combined_tasks = result.get("tasks", []) if not is_broadcast else primary_result.get("tasks", [])

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
        task["tasks"] = combined_tasks

    except Exception as e:
        import traceback
        print(f"[run_nl_task] FATAL task={task_id[:8]}: {type(e).__name__}: {e}")
        traceback.print_exc()
        err_text = str(e) or type(e).__name__
        task["status"] = "error"
        task["answer"] = f"Ошибка: {err_text}"
        task["commands"] = []
        add_message(chat_id, "assistant", task["answer"])


# ── Onboarding task (без устройств) ──────────────────────────────────────

async def run_onboarding_task(task_id: str, user_id: int, message: str, chat_id: int):
    """
    Фоновая задача для онбординг-режима (нет устройств).
    Простой чат с LLM без tool-вызовов.
    """
    task = tasks[task_id]
    try:
        chat_history = get_messages(chat_id, limit=50)
        result = await process_onboarding_message(
            user_message=message,
            chat_history=chat_history,
        )
        answer = result.get("answer", "")
        task["status"] = "done"
        task["answer"] = answer
        task["commands"] = []
        add_message(chat_id, "assistant", answer)
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
    return HTMLResponse("<h1>ИРУ v3.5 — UI не найден</h1>")


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

def _client_ip(request: Request) -> str:
    """Get client IP (supports X-Forwarded-For behind Caddy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/auth")
async def auth(body: AuthRequest, request: Request):
    """
    Авторизация по статичному токену.
    Возвращает JWT access + refresh токены.
    Старый формат ответа сохранён для совместимости (user.token доступен).
    """
    client_ip = _client_ip(request)
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Подождите минуту.")
    user = get_user_by_token(body.token)
    if not user:
        add_audit_log(None, None, "login_failed",
                      f"token={body.token[:8]}...", _client_ip(request))
        return JSONResponse(
            status_code=401,
            content={"status": "error", "error": "Недействительный токен"}
        )
    plan = user.get("plan") or "free"
    access  = create_access_token(user["id"], user["name"], plan)
    refresh = create_refresh_token()
    store_refresh_token(user["id"], refresh, REFRESH_TTL)
    add_audit_log(user["id"], user["name"], "login", None, _client_ip(request))
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "token": user["token"],
            "data_consent": bool(user.get("data_consent", 0)),
        },
        "access_token":  access,
        "refresh_token": refresh,
        "expires_in":    ACCESS_TTL,
    }


@app.post("/api/refresh")
async def refresh_token_endpoint(body: RefreshRequest, request: Request):
    """Обновить access token по refresh token."""
    rt = get_refresh_token(body.refresh_token)
    if not rt:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "error": "Refresh token недействителен или истёк"}
        )
    user = get_user_by_id(rt["user_id"])
    if not user:
        revoke_refresh_token(body.refresh_token)
        return JSONResponse(
            status_code=401,
            content={"status": "error", "error": "Пользователь не найден"}
        )
    plan = user.get("plan") or "free"
    access = create_access_token(user["id"], user["name"], plan)
    add_audit_log(user["id"], user["name"], "token_refresh", None, _client_ip(request))
    return {
        "status": "ok",
        "access_token": access,
        "expires_in":   ACCESS_TTL,
    }


@app.post("/api/logout")
async def logout(body: LogoutRequest, request: Request):
    """Выход — отзыв refresh token."""
    revoke_refresh_token(body.refresh_token)
    # Попробуем идентифицировать пользователя для лога
    try:
        user = get_current_user(request)
        add_audit_log(user["id"], user["name"], "logout", None, _client_ip(request))
    except Exception:
        add_audit_log(None, None, "logout", None, _client_ip(request))
    return {"status": "ok"}


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
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    users = list_users()
    return {"status": "ok", "users": users}


@app.post("/api/admin/users")
async def admin_create_user(body: CreateUserRequest, request: Request):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    new_user = create_user(body.name)
    add_audit_log(user["id"], user["name"], "admin_create_user",
                  f"new_user={body.name} id={new_user['id']}", _client_ip(request))
    return {"status": "ok", "user": new_user}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    ok = delete_user(user_id)
    if ok:
        add_audit_log(user["id"], user["name"], "admin_delete_user",
                      f"deleted_user_id={user_id}", _client_ip(request))
    return {"status": "ok" if ok else "error", "deleted": ok}


@app.get("/api/admin/training")
async def admin_training_data(request: Request, limit: int = 100, offset: int = 0):
    """Записи обучения (только для администратора)."""
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    data = get_training_data(limit, offset)
    count = get_training_count()
    return {"status": "ok", "data": data, "total": count}


# ── DEVICES API ──────────────────────────────────────────────────────────

@app.get("/api/devices")
async def get_devices_api(request: Request):
    user = get_current_user(request)
    user_devs = get_user_devices(user["id"])
    print(f"[api/devices] user_id={user['id']}, user_devs={list(user_devs.keys())}")
    result = {}
    for dk_key, dev in user_devs.items():
        short_did = dev.get("short_device_id", _short_did(dk_key))
        result[short_did] = {
            "device_id": short_did,
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
    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    # Проверка дневного лимита команд (тарифный план, admin без ограничений)
    if not _is_admin(user):
        cmd_limit = check_daily_command_limit(user["id"])
        if not cmd_limit["allowed"]:
            return {
                "status": "error",
                "error": f"Дневной лимит команд исчерпан ({cmd_limit['used']}/{cmd_limit['limit']}). Обновите тариф для снятия ограничений."
            }

        # Увеличить счётчик команд
        increment_daily_commands(user["id"])

    cmd_dk = _dk(user["id"], cmd.device_id)
    dev = devices.get(cmd_dk)
    if not dev:
        return {"status": "error", "error": "Устройство не найдено или нет доступа"}
    try:
        result = await send_command_to_agent(cmd_dk, cmd.action, cmd.params)
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
    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    # Проверка дневного лимита команд (тарифный план, admin без ограничений)
    if not _is_admin(user):
        cmd_limit = check_daily_command_limit(user["id"])
        if not cmd_limit["allowed"]:
            return {
                "status": "error",
                "error": f"Дневной лимит команд исчерпан ({cmd_limit['used']}/{cmd_limit['limit']}). Обновите тариф для снятия ограничений."
            }
        increment_daily_commands(user["id"])

    # Определить целевые устройства
    user_devs = get_user_devices(user["id"])

    # Диагностика: логируем состояние устройств при каждом запросе
    print(f"[nl_command] user_id={user['id']}, user='{user['name']}', "
          f"cmd.device_id='{cmd.device_id}', "
          f"user_devs={list(user_devs.keys())}, "
          f"all_devices_keys={list(devices.keys())}")

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

    # Режим без устройств (onboarding) — только если реально нет устройств
    # И пользователь не указал конкретное устройство
    if not user_devs and not cmd.device_id:
        task_id = str(uuid.uuid4())[:12]
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user["id"],
            "chat_id": chat_id,
            "message": cmd.message,
            "device_ids": [],
            "status": "running",
            "results": {},
            "answer": None,
            "commands": None,
            "created_at": time.time(),
        }
        asyncio.create_task(run_onboarding_task(task_id, user["id"], cmd.message, chat_id))
        return {
            "status": "ok",
            "task_id": task_id,
            "chat_id": chat_id,
            "device_ids": [],
        }

    if cmd.broadcast:
        # Все устройства пользователя
        target_ids = list(user_devs.keys())
    elif cmd.device_ids:
        # Конкретные устройства
        target_ids = [_dk(user["id"], did) for did in cmd.device_ids if _dk(user["id"], did) in user_devs]
    else:
        # Одно устройство (как раньше)
        cmd_dk = _dk(user["id"], cmd.device_id)
        if cmd_dk not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено или нет доступа"}
        target_ids = [cmd_dk]

    if not target_ids:
        return {"status": "error", "error": "Нет доступных устройств"}

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
        "modes": cmd.modes or {},
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
            "tasks": task.get("tasks", []),
            "results": task.get("results", {}),
            "confirm_data": task.get("confirm_data"),
            "created_at": task["created_at"],
        }
    }


@app.post("/api/tasks/{task_id}/confirm")
async def api_confirm_task(task_id: str, request: Request):
    """Пользователь подтвердил опасную команду."""
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "confirm":
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    cd = task.get("confirm_data", {})
    short_did = cd.get("device_id", "")
    params = cd.get("params", {})
    chat_id = cd.get("chat_id", task.get("chat_id"))

    # Восстановить составной ключ из user_id задачи и короткого device_id
    confirm_dk = _dk(task["user_id"], short_did) if ":" not in short_did else short_did

    task["status"] = "running"

    async def execute_confirmed():
        try:
            result = await send_command_to_agent(
                confirm_dk, "execute_cmd", params, skip_confirm=True
            )
            cmd_entry = {
                "command": cd.get("command", ""),
                "device_id": short_did,
                "result": result,
            }
            existing_cmds = task.get("commands", []) or []
            existing_cmds.append(cmd_entry)
            task["commands"] = existing_cmds

            ok = not result.get("error")
            task["answer"] = "Выполнено." if ok else f"Ошибка: {result.get('error', '')}"
            task["status"] = "done"
            add_message(chat_id, "assistant", task["answer"], task["commands"])
        except Exception as e:
            task["status"] = "error"
            task["answer"] = f"Ошибка: {str(e)}"
            add_message(chat_id, "assistant", task["answer"])

    asyncio.create_task(execute_confirmed())
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/deny")
async def api_deny_task(task_id: str, request: Request):
    """Пользователь отклонил опасную команду."""
    user = get_current_user(request)
    task = tasks.get(task_id)
    if not task or task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "confirm":
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    chat_id = task.get("confirm_data", {}).get("chat_id", task.get("chat_id"))
    task["status"] = "done"
    task["answer"] = "Команда отменена пользователем."
    add_message(chat_id, "assistant", task["answer"], task.get("commands", []))
    return {"status": "ok"}


# ── DOWNLOAD API ─────────────────────────────────────────────────────────

@app.get("/api/download/{token}")
async def download_file(token: str):
    info = download_tokens.pop(token, None)
    if not info:
        return {"status": "error", "error": "Ссылка недействительна или истекла"}

    if time.time() - info["created"] > TOKEN_TTL:
        return {"status": "error", "error": "Ссылка истекла"}

    short_did = info["device_id"]
    file_path = info["file_path"]
    dl_user_id = info.get("user_id", 0)

    # Составной ключ для поиска в devices
    device_key = _dk(dl_user_id, short_did) if dl_user_id else short_did

    try:
        result = await send_command_to_agent(
            device_key, "get_file_content", {"path": file_path}
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

    dl_dk = _dk(user["id"], device_id)
    dev = devices.get(dl_dk)
    if not dev:
        return {"status": "error", "error": "Нет доступа к устройству"}

    token = create_download_token(device_id, file_path, user_id=user["id"])
    return {"status": "ok", "url": f"/api/download/{token}"}


# ── Скачивание агента ─────────────────────────────────────────────────────

AGENT_DOWNLOAD_DIR = Path("/opt/iru/app/exe")

@app.get("/api/download_agent")
async def download_agent(request: Request):
    """Скачать архив агента (только авторизованным)."""
    user = get_current_user(request)

    # Ищем первый .zip или .exe в папке
    if not AGENT_DOWNLOAD_DIR.exists():
        raise HTTPException(status_code=404, detail="Файл агента не найден")

    # Приоритет: .zip > .exe
    archive = None
    for ext in ("*.zip", "*.exe"):
        files = sorted(AGENT_DOWNLOAD_DIR.glob(ext), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            archive = files[0]
            break

    if not archive:
        raise HTTPException(status_code=404, detail="Файл агента не найден")

    return FileResponse(
        path=str(archive),
        filename=archive.name,
        media_type="application/octet-stream",
    )


# ── USER INFO & PLANS ────────────────────────────────────────────────────

@app.get("/api/user_info")
async def api_user_info(request: Request):
    """Информация о пользователе: план, лимиты, статус соглашения."""
    user = get_current_user(request)
    plan = get_user_plan(user["id"])
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    cmd_usage = check_daily_command_limit(user["id"])
    dev_count = len(get_user_devices(user["id"]))
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "plan": plan,
            "limits": limits,
            "commands_today": cmd_usage["used"],
            "commands_limit": cmd_usage["limit"],
            "devices_count": dev_count,
            "devices_limit": limits["max_devices"],
            "terms_accepted": has_accepted_terms(user["id"]),
        }
    }


@app.post("/api/accept_terms")
async def api_accept_terms(request: Request):
    """Принять пользовательское соглашение."""
    user = get_current_user(request)
    accept_terms(user["id"])
    return {"status": "ok"}


@app.get("/api/terms_status")
async def api_terms_status(request: Request):
    """Проверить, принял ли пользователь соглашение."""
    user = get_current_user(request)
    return {"status": "ok", "accepted": has_accepted_terms(user["id"])}


class SetPlanRequest(BaseModel):
    plan: str

@app.patch("/api/admin/users/{user_id}/plan")
async def api_admin_set_plan(user_id: int, body: SetPlanRequest, request: Request):
    """Изменить план пользователя (только admin)."""
    admin = get_current_user(request)
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только администратор")
    if body.plan not in PLAN_LIMITS:
        return {"status": "error", "error": f"Неизвестный план: {body.plan}. Доступны: free, pro, business"}
    ok = set_user_plan(user_id, body.plan)
    if not ok:
        return {"status": "error", "error": "Пользователь не найден"}
    add_audit_log(admin["id"], admin["name"], "admin_set_plan",
                  f"user_id={user_id} plan={body.plan}", _client_ip(request))
    return {"status": "ok", "user_id": user_id, "plan": body.plan}


@app.get("/api/admin/audit")
async def api_admin_audit(request: Request,
                          limit: int = Query(100, ge=1, le=500),
                          offset: int = Query(0, ge=0),
                          user_id: Optional[int] = Query(None)):
    """Аудит-лог (только admin)."""
    admin = get_current_user(request)
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администратора")
    logs = get_audit_log(limit=limit, offset=offset, user_id=user_id)
    total = get_audit_log_count(user_id=user_id)
    return {"status": "ok", "logs": logs, "total": total}


# ── Device Profiles API ───────────────────────────────────────────────

@app.get("/api/device_profiles")
async def api_device_profiles(request: Request):
    """Получить профили устройств текущего пользователя. Admin видит все."""
    user = get_current_user(request)
    if _is_admin(user):
        # Admin: можно фильтровать по user_id, иначе все профили
        from database import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM device_profiles ORDER BY updated_at DESC"
            ).fetchall()
            profiles = []
            for row in rows:
                d = dict(row)
                if d.get("disks"):
                    try:
                        import json as _json
                        d["disks"] = _json.loads(d["disks"])
                    except Exception:
                        pass
                profiles.append(d)
    else:
        profiles = get_user_device_profiles(user["id"])
    return {"status": "ok", "profiles": profiles}


@app.get("/api/device_profiles/{device_id}")
async def api_device_profile(device_id: str, request: Request):
    """Получить профиль конкретного устройства."""
    user = get_current_user(request)
    profile = get_device_profile(device_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Профиль устройства не найден")
    # Проверка доступа: владелец или admin
    if not _is_admin(user) and profile.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Нет доступа")
    return {"status": "ok", "profile": profile}


# ── РЕЖИМ РАЗРАБОТЧИКА (RAW CMD) ────────────────────────────────────────

class RawCommand(BaseModel):
    command: str
    device_id: str = ""       # конкретное устройство
    broadcast: bool = False   # на все устройства

@app.post("/api/raw_command")
async def api_raw_command(cmd: RawCommand, request: Request):
    """
    Режим разработчика: прямая отправка CMD/PowerShell-команды на устройство.
    Требует plan=pro или business.
    Поддерживает broadcast (на все устройства).
    История не сохраняется.
    """
    user = get_current_user(request)

    # Проверка плана (admin без ограничений)
    if not _is_admin(user):
        plan = get_user_plan(user["id"])
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        if not limits.get("dev_mode"):
            return {"status": "error", "error": "Режим разработчика доступен только на тарифе Pro или Business."}

        # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_ip_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="IP rate limit: 10 req/min")
    if not check_rate_limit(str(user["id"])):
        return {"status": "error", "error": "Слишком много запросов. Подождите минуту."}

    if not cmd.command.strip():
        return {"status": "error", "error": "Команда не может быть пустой"}

    user_devs = get_user_devices(user["id"])
    if not user_devs:
        return {"status": "error", "error": "Нет подключённых устройств"}

    # Определить целевые устройства
    if cmd.broadcast:
        target_ids = list(user_devs.keys())
    else:
        cmd_dk = _dk(user["id"], cmd.device_id)
        if cmd_dk not in user_devs:
            return {"status": "error", "error": f"Устройство '{cmd.device_id}' не найдено"}
        target_ids = [cmd_dk]

    # UTF-8 вывод (PowerShell-совместимый)
    raw_cmd = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; {cmd.command}"

    # Выполнить на каждом устройстве
    results = {}
    async def exec_on_device(did):
        try:
            result = await send_command_to_agent(
                did, "execute_cmd", {"command": raw_cmd},
                user_id=user["id"]
            )
            results[did] = {"status": "ok", "result": result}
        except Exception as e:
            error_str = str(e)
            if "CONFIRM_REQUIRED" in error_str:
                results[did] = {"status": "confirm_required", "command": cmd.command, "error": error_str}
            elif "BLOCKED" in error_str:
                results[did] = {"status": "blocked", "error": error_str}
            else:
                results[did] = {"status": "error", "error": error_str}

    await asyncio.gather(*[exec_on_device(did) for did in target_ids])

    add_audit_log(user["id"], user["name"], "raw_command",
                  f"cmd={cmd.command[:120]} devices={target_ids}", _client_ip(request))

    return {
        "status": "ok",
        "results": results,
        "broadcast": cmd.broadcast,
        "device_count": len(target_ids),
    }




# ── Автообновление агента ─────────────────────────────────────────────

UPDATES_DIR = Path(__file__).parent / "updates"

@app.get("/api/agent/version")
async def api_agent_version():
    """Проверка актуальной версии агента. Не требует авторизации."""
    version_file = UPDATES_DIR / "version.json"
    if not version_file.exists():
        return {"version": "0.0", "min_version": "0.0", "changelog": "",
                "download_url": "", "kind": "exe"}
    data = json.loads(version_file.read_text(encoding="utf-8"))
    data["download_url"] = "/api/agent/download"
    # Обратная совместимость: если kind отсутствует — exe
    if "kind" not in data:
        data["kind"] = "exe"
    return data


@app.get("/api/agent/download")
async def api_agent_download():
    """Скачать последнюю версию агента (exe или zip). Не требует авторизации."""
    version_file = UPDATES_DIR / "version.json"
    if not version_file.exists():
        raise HTTPException(status_code=404, detail="Файл версии не найден")
    data = json.loads(version_file.read_text(encoding="utf-8"))
    filename = data.get("filename", "agent.exe")
    kind = data.get("kind", "exe")
    file_path = UPDATES_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл агента не найден на сервере")
    media_type = "application/zip" if kind == "zip" else "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


@app.post("/api/agent/upload")
async def api_agent_upload(request: Request, version: str = Query(...)):
    """Загрузить новую версию агента (exe или zip). Только admin.
    Тип определяется по сигнатуре: PK\\x03\\x04 = zip, иначе exe."""
    user = get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Только для администратора")
    body = await request.body()
    if len(body) < 1000:
        raise HTTPException(status_code=400, detail="Файл слишком маленький")
    if len(body) > 100_000_000:
        raise HTTPException(status_code=400, detail="Файл слишком большой (>100МБ)")
    UPDATES_DIR.mkdir(exist_ok=True)
    # Определяем тип по ZIP-сигнатуре (PK\x03\x04)
    is_zip = body[:4] == b"PK\x03\x04"
    if is_zip:
        kind = "zip"
        filename = "agent.zip"
    else:
        kind = "exe"
        filename = "agent.exe"
    save_path = UPDATES_DIR / filename
    save_path.write_bytes(body)
    # Обновить version.json
    version_data = {
        "version": version,
        "min_version": "3.0",
        "changelog": "",
        "filename": filename,
        "kind": kind,
    }
    version_file = UPDATES_DIR / "version.json"
    if version_file.exists():
        try:
            old = json.loads(version_file.read_text(encoding="utf-8"))
            version_data["min_version"] = old.get("min_version", "3.0")
            version_data["changelog"] = old.get("changelog", "")
        except Exception:
            pass
    version_data["version"] = version
    version_file.write_text(json.dumps(version_data, ensure_ascii=False, indent=2), encoding="utf-8")
    add_audit_log(user["id"], user["name"], "agent_upload",
                  f"version={version}, kind={kind}, size={len(body)}", None)
    return {"status": "ok", "version": version, "kind": kind, "size": len(body)}


# ── WebSocket для агентов ────────────────────────────────────────────────

@app.websocket("/ws/{device_id}")
async def websocket_agent(ws: WebSocket, device_id: str, user_token: str = Query(default="")):
    user = get_user_by_token(user_token) if user_token else None
    if not user:
        await ws.close(code=4001, reason="Недействительный токен пользователя")
        return

    # Составной ключ для изоляции устройств между пользователями
    dk = _dk(user["id"], device_id)

    # Проверка лимита устройств (тарифный план)
    if not _is_admin(user):
        current_count = len(get_user_devices(user["id"]))
        dev_limit = check_device_limit(user["id"], current_count)
        if not dev_limit["allowed"] and dk not in devices:
            await ws.close(code=4003, reason=f"Лимит устройств исчерпан ({dev_limit['current']}/{dev_limit['limit']})")
            return

    await ws.accept()
    print(f"[ws] agent connected: device_id='{device_id}', user_id={user['id']}, user='{user['name']}'")
    add_audit_log(user["id"], user["name"], "agent_connect",
                  f"device={device_id}", None)

    devices[dk] = {
        "ws": ws,
        "info": {},
        "pending": {},
        "user_id": user["id"],
        "short_device_id": device_id,
    }
    print(f"[ws] devices after connect: {list(devices.keys())}")

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "register":
                payload = data.get("payload", {})
                devices[dk]["info"] = payload
                # Сохранить device profile в БД
                try:
                    upsert_device_profile(device_id, user["id"], payload)
                    print(f"[ws] device profile saved: {device_id}")
                except Exception as e:
                    print(f"[ws] failed to save device profile: {e}")
                print(f"[ws] registered: {device_id} — {data.get('payload', {})}")

            elif msg_type == "result":
                payload = data.get("payload", {})
                cmd_id = payload.get("id")
                future = devices[dk]["pending"].pop(cmd_id, None)
                if future and not future.done():
                    if payload.get("status") == "ok":
                        future.set_result(payload.get("result", {}))
                    else:
                        future.set_result({"error": payload.get("error", "Неизвестная ошибка")})

    except WebSocketDisconnect:
        print(f"[ws] agent disconnected: device_id='{device_id}', user_id={user['id']}")
    except Exception as e:
        print(f"[ws] error for {device_id}: {e}")
    finally:
        # Удалять только если ws в devices — это именно наш сокет
        # (защита от race condition при быстром reconnect)
        current = devices.get(dk)
        if current and current.get("ws") is ws:
            devices.pop(dk, None)
            print(f"[ws] device removed: {device_id}")
        else:
            print(f"[ws] device already reconnected, keeping: {device_id}")
        print(f"[ws] devices after disconnect: {list(devices.keys())}")
        add_audit_log(user["id"], user["name"], "agent_disconnect",
                      f"device={device_id}", None)


# Раздача статики из ui/ по корневому пути (для относительных путей в index.html)
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="ui_root")

# ── Запуск ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

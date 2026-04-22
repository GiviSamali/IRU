"""
controller.py — LLM-планировщик ИРУ v3.5

Принимает текстовую задачу пользователя, через DeepSeek переводит в
последовательность команд PowerShell/cmd, отправляет агенту на выполнение,
анализирует результаты и формирует финальный ответ.

Два инструмента для LLM:
  - execute_cmd: выполнить команду на устройстве
  - get_file_link: получить ссылку для скачивания файла с устройства

Поддержка:
  - Мультиустройства (LLM знает все подключённые устройства пользователя)
  - Память чатов (последние 50 сообщений подаются в контекст)

Макс 5 итераций (tool-call loop).
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from . import database as db  # type: ignore
except ImportError:
    import database as db  # type: ignore


_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
]


def _current_datetime_msk() -> str:
    """Текущая дата/время в московской таймзоне, на русском языке."""
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    weekday = _WEEKDAYS_RU[now.weekday()]
    month = _MONTHS_RU[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year}, {now.strftime('%H:%M')} MSK"


def _collect_tasks(task_ids: list[int]) -> list[dict]:
    """Подгрузить текущее состояние задач для ответа UI."""
    result = []
    for tid in task_ids:
        try:
            t = db.get_task(tid)
            if t:
                result.append({
                    "id": t["id"],
                    "goal": t["goal"],
                    "status": t["status"],
                    "steps": [
                        {"idx": s["idx"], "description": s["description"],
                         "status": s["status"], "summary": s.get("summary")}
                        for s in t["steps"]
                    ],
                })
        except Exception as e:
            print(f"[llm] _collect_tasks error for task {tid}: {e}")
    return result


def _set_current_step(poll_task_id: str | None, text: str) -> None:
    """Обновить task.current_step для отображения live-прогресса в UI."""
    if not poll_task_id:
        return
    try:
        from main import tasks          # локальный импорт — избегаем циклических зависимостей
        t = tasks.get(poll_task_id)
        if t:
            t["current_step"] = text
    except Exception:
        pass


class ConfirmationRequired(Exception):
    """Команда требует подтверждения пользователя."""
    def __init__(self, command: str, device_id: str, params: dict,
                 answer: str, commands_log: list):
        self.command = command
        self.device_id = device_id
        self.params = params
        self.answer = answer          # LLM-ответ до момента подтверждения
        self.commands_log = commands_log  # уже выполненные команды
        super().__init__(f"Подтверждение: {command[:80]}")
import asyncio
import httpx
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "llm_config.json"


# ── Конфигурация LLM ────────────────────────────────────────────────────

def load_llm_config() -> dict:
    """Загрузить конфиг LLM из llm_config.json.
    API key берётся из переменной окружения DEEPSEEK_API_KEY (приоритет)
    или из поля api_key в llm_config.json (фоллбэк).
    """
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
    return cfg


# ── Системный промпт (шаблон) ───────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
Ты — ИРУ (Интеллектуальный Режим Управления), ИИ-ассистент для управления \
компьютерами пользователя через командную строку.

## Текущая дата и время
Сейчас: {current_datetime_msk} (Москва, MSK).
Используй эту дату для запросов про "сегодня", "сейчас", "последние \
новости". НЕ полагайся на свою память о текущем годе — она устарела.

## Подключённые устройства
{devices_block}

## Текущее устройство (по умолчанию)
ID: {current_device_id}
Hostname: {current_hostname}
ОС: {current_os} ({current_os_version})
{device_profile_block}
## Доступные инструменты

### 1. execute_cmd
Выполнить команду на устройстве.
- command (string, обязательно): команда для выполнения
- timeout (integer, по умолчанию 30): таймаут в секундах
- shell (string, по умолчанию "auto"): "powershell", "cmd" или "bash"
- device_id (string, опционально): ID устройства. Если не указан — \
выполняется на текущем устройстве.

### 2. write_content
Напрямую записать длинный текст в файл без шелла (нет вопросов экранирования и кодировок). \
Используй этот инструмент для любых файлов >200 символов, многострочных текстов, \
кода, JSON, HTML — вместо Set-Content / echo / heredoc.
- path (string, обязательно): полный путь к файлу
- content (string, обязательно): содержимое файла
- append (boolean, по умолчанию false): true = дописать в конец файла; \
false = перезаписать. Если ответ LLM оборвался по длине — допиши оставшееся с append=true.
- encoding (string, по умолчанию "utf-8")
- device_id (string, опционально): ID устройства

### 3. get_file_link
Получить временную ссылку для скачивания файла с устройства.
- file_path (string, обязательно): полный путь к файлу на устройстве
- device_id (string, опционально): ID устройства

## Общие правила
1. Пользователь описывает задачу на естественном языке.
2. Определи, на каком устройстве нужно выполнить задачу. Если пользователь \
указывает конкретное устройство (по имени, hostname или ID) — используй \
параметр device_id. Если не указывает — выполни на текущем устройстве.
3. Анализируй результат каждой команды перед следующим шагом.
4. Если команда завершилась ошибкой — попробуй другой подход (макс. 8 итераций).
5. По завершении — дай короткий понятный ответ на русском языке.
6. Если получишь ошибку BLOCKED — сообщи пользователю, что эта команда недоступна в бета-тестировании. \
Если получишь CONFIRM_REQUIRED — ОСТАНОВИСЬ, не повторяй команду и не пытайся её переформулировать.
7. Если задача не связана с компьютером — просто ответь текстом.
8. Если пользователь просит скачать/передать файл — используй get_file_link.
9. У тебя есть память — ты помнишь предыдущие сообщения в этом чате. \
Используй контекст разговора для более точных ответов.
10. НИКОГДА не используй Markdown-разметку в ответах: никаких **, *, #, ```, - и т.д. \
Отвечай чистым текстом без форматирования.
11. Для путей к рабочему столу и папкам пользователя — ВСЕГДА используй путь из \
профиля устройства (раздел "Профиль устройства" выше), а не переменные окружения. \
Если в профиле указан конкретный путь к рабочему столу — используй именно его.
12. Для работы с приложениями используй программные интерфейсы, а не эмуляцию клавиш. \
Философия: научить машину быть машиной — никакой эмуляции пользователя.
13. ЗАПИСЬ ТЕКСТА В ФАЙЛЫ: для любого текста длиннее 200 символов или с переносами строк \
ИСПОЛЬЗУЙ инструмент write_content, а НЕ execute_cmd с Set-Content/echo/heredoc. \
write_content не требует экранирования кавычек/переносов и работает одинаково на Windows и Linux. \
Если текст очень большой и не помещается в один ответ — первый вызов с append=false, \
дальше append=true для каждой следующей части.
14. КОНВЕЙЕР (многошаговые задачи): если задача требует 3+ разных действий или чётко \
делится на шаги ("собери данные и сделай отчёт", "установи X, сконфигурируй, проверь") — \
СНАЧАЛА вызови create_plan с чётким списком шагов (3-10 штук, каждый в одну строку в формате \
глагол+деталь). Затем выполняй шаги по очереди через execute_cmd/write_content, и после \
каждого закрытого шага вызывай mark_step(task_id, idx, status="done"|"failed", summary). \
Простые задачи (1-2 действия) делай без плана — не засоряй UI.
15. Для создания текстовых файлов (.txt, .md) ВСЕГДА используй инструмент write_content. \
ЗАПРЕЩЕНО создавать текстовые файлы через PowerShell с New-Object -ComObject Word.Application, \
Word.Selection.TypeText, Word.Selection.TypeParagraph. Эти методы приводят к падению агента. \
Для больших текстов — только write_content.
16. Для поиска информации в интернете используй ТОЛЬКО инструмент web_search. ЗАПРЕЩЕНО \
использовать Invoke-WebRequest, curl, wget для поиска (duckduckgo.com, google.com/search, \
bing.com/search и т.п.) — это не работает и возвращает мусор. Если нет актуальной информации — \
вызывай web_search.

## Специфичные правила для ОС текущего устройства
{os_rules}
"""


WINDOWS_RULES = """\
Текущее устройство работает под Windows. Используй PowerShell.

W1. Кодировка: ВСЕГДА добавляй в начало КАЖДОЙ команды PowerShell: \
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; \
Это обязательно для корректного отображения русского текста. \
При чтении/записи файлов явно указывай кодировку: \
Get-Content -Path file -Encoding UTF8; Set-Content -Path file -Encoding UTF8 -Value $text.

W2. Для работы с приложениями используй программные интерфейсы (COM, WMI). Примеры:
  - Открыть Word и вставить текст: $w = New-Object -ComObject Word.Application; $w.Visible = $true; \
$d = $w.Documents.Add(); $d.Content.Text = 'текст'
  - Открыть Excel: $xl = New-Object -ComObject Excel.Application; $xl.Visible = $true; \
$wb = $xl.Workbooks.Add()
  - Открыть Notepad и вставить: Start-Process notepad; Start-Sleep 1; \
(Get-Process notepad).MainWindowTitle для проверки. Для записи в Notepad — сохрани текст в файл \
и открой его: Set-Content -Path $env:TEMP\\text.txt -Value 'текст'; \
Start-Process notepad $env:TEMP\\text.txt
  - Получить активное окно: Add-Type -Name W -Namespace U -Member \
'[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); \
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, \
System.Text.StringBuilder t, int m);'; $h=[U.W]::GetForegroundWindow(); \
$sb=New-Object System.Text.StringBuilder 256; [U.W]::GetWindowText($h,$sb,256); $sb.ToString()

W3. ЗАПРЕЩЕНО использовать here-string синтаксис (@'...'@ или @"..."@) в командах. \
Here-string требует переноса строки после открывающего маркера, а команды передаются \
одной строкой — это всегда вызывает ошибку. Вместо этого:
  - Для многострочного текста: используй Set-Content с экранированными строками, \
например: Set-Content -Path file.txt -Value ("строка1`nстрока2`nстрока3") -Encoding UTF8
  - Для длинных строк: используй конкатенацию через +, или переменные.
  - Для JSON: формируй строку напрямую, например: $json = '{{"key": "value"}}'; \
Set-Content -Path file.json -Value $json -Encoding UTF8

W4. Пути к рабочему столу: на многих машинах рабочий стол перенесён в OneDrive \
и $env:USERPROFILE\\Desktop не существует. ВСЕГДА используй путь из профиля устройства \
(например C:\\Users\\user\\OneDrive\\Desktop).
"""


LINUX_RULES = """\
Текущее устройство работает под Linux. Используй bash.

L1. Все команды выполняются через bash. Кодировка по умолчанию UTF-8, \
дополнительных префиксов не требуется.

L2. Для работы с приложениями используй нативные CLI-инструменты и D-Bus, \
а не эмуляцию клавиш. Примеры:
  - Открыть файл в ассоциированном приложении: xdg-open /path/to/file
  - Создать документ и открыть: echo 'текст' > /tmp/doc.txt && xdg-open /tmp/doc.txt
  - Открыть URL в браузере: xdg-open 'https://example.com'
  - Управление окнами (если есть wmctrl): wmctrl -l для списка окон, \
wmctrl -a 'Title' для активации окна.
  - Запуск GUI-приложений: nohup gedit /tmp/file.txt >/dev/null 2>&1 & \
(отсоединение от шелла, иначе процесс умрёт вместе с сессией).

L3. Для многострочного текста используй heredoc или printf:
  - Heredoc: cat > /tmp/file.txt <<'EOF'\nстрока1\nстрока2\nEOF
  - Printf: printf 'строка1\\nстрока2\\n' > /tmp/file.txt
  - Для JSON: echo '{{"key": "value"}}' > /tmp/data.json

L4. Пути: используй пути из профиля устройства (раздел "Профиль устройства"). \
Рабочий стол обычно ~/Desktop или ~/Рабочий стол (зависит от локали). \
Домашняя директория: $HOME. Никогда не полагайся на жёсткие пути типа /home/user — \
имя пользователя бери из профиля.

L5. Права и sudo: НЕ используй sudo без явного запроса пользователя. Для установки \
пакетов и системных действий сначала спроси подтверждения у пользователя текстом.

L6. Для поиска файлов используй find, для текста — grep/rg. \
Для работы с процессами — ps, pgrep, pkill.
"""


# ── Определения инструментов ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Создать план из шагов для многошаговой задачи. Вызывай В САМОМ НАЧАЛЕ, до любых execute_cmd/write_content. Возвращает task_id. Далее по каждому шагу вызывай mark_step(task_id, idx, status, summary).",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Общая цель задачи одной строкой"
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "План: массив коротких описаний шагов (3-10 шт), каждое — одно действие"
                    }
                },
                "required": ["goal", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_step",
            "description": "Обновить статус шага плана (вызывай после выполнения каждого шага).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи из create_plan"},
                    "idx": {"type": "integer", "description": "Номер шага (0-based)"},
                    "status": {
                        "type": "string",
                        "enum": ["running", "done", "failed", "skipped"],
                        "description": "Новый статус шага"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Короткое описание результата шага"
                    }
                },
                "required": ["task_id", "idx", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_content",
            "description": "Напрямую записать текст в файл без шелла. Используй для длинных и/или многострочных текстов (>200 символов) вместо Set-Content/echo/heredoc. Поддерживает append для добавления частей по сегментам.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Полный путь к файлу"
                    },
                    "content": {
                        "type": "string",
                        "description": "Содержимое для записи"
                    },
                    "append": {
                        "type": "boolean",
                        "description": "true = дописать в конец; false = перезаписать. По умолчанию false.",
                        "default": False
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Кодировка файла",
                        "default": "utf-8"
                    },
                    "device_id": {
                        "type": "string",
                        "description": "ID устройства. Если не указан — текущее."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_cmd",
            "description": "Выполнить команду в PowerShell/cmd/bash на устройстве пользователя",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Команда для выполнения"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Таймаут в секундах (по умолчанию 30)",
                        "default": 30
                    },
                    "shell": {
                        "type": "string",
                        "enum": ["auto", "powershell", "cmd", "bash"],
                        "description": "Шелл для выполнения (по умолчанию auto)",
                        "default": "auto"
                    },
                    "device_id": {
                        "type": "string",
                        "description": "ID устройства для выполнения. Если не указан — текущее устройство."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_link",
            "description": "Получить временную ссылку для скачивания файла с устройства",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Полный путь к файлу на устройстве"
                    },
                    "device_id": {
                        "type": "string",
                        "description": "ID устройства. Если не указан — текущее."
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поиск актуальной информации в интернете через Tavily. Используй ВСЕГДА, когда нужны свежие факты, новости, документация, сведения о продуктах, о людях, о событиях. Единственный допустимый способ искать в интернете. НИКОГДА не выполняй Invoke-WebRequest/curl/wget для поиска — только web_search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"},
                    "max_results": {"type": "integer", "description": "Сколько результатов вернуть (по умолчанию 5, максимум 10)", "default": 5}
                },
                "required": ["query"]
            }
        }
    }
]

MAX_ITERATIONS = 20


def _pick_model(cfg: dict, modes: dict | None) -> str:
    """Выбрать модель LLM: deepseek-reasoner для сложных режимов, deepseek-chat иначе."""
    base = cfg.get("model", "deepseek-chat")
    reasoner = cfg.get("model_reasoner", "deepseek-reasoner")
    is_complex = bool(modes) and (modes.get("pipeline") or modes.get("autonomous"))
    return reasoner if is_complex else base


# ── Промпт для режима без устройств (помощник по настройке) ─────────────────

ONBOARDING_PROMPT = """\
Ты — ИРУ (Интеллектуальный Режим Управления), ИИ-ассистент для управления \
компьютером через естественный язык.

## Текущая дата и время
Сейчас: {current_datetime_msk} (Москва, MSK).
Используй эту дату для запросов про "сегодня", "сейчас", "последние \
новости". НЕ полагайся на свою память о текущем годе — она устарела.

Сейчас у пользователя НЕТ подключённых устройств. Твоя главная задача — помочь \
ему подключить первое устройство.

## Инструкция по подключению

{instruction_text}

## Правила
1. Отвечай на русском языке, коротко и по делу.
2. Если пользователь просит выполнить команду на компьютере — объясни, что сначала \
нужно подключить устройство, и помоги это сделать.
3. Если пользователь задаёт общий вопрос (что ты умеешь, как работаешь) — ответь и напомни, \
что для полноценной работы нужно подключить устройство.
4. НИКОГДА не используй Markdown-разметку в ответах: никаких **, *, #, ```, - и т.д. \
Отвечай чистым текстом без форматирования.
5. Будь дружелюбным и терпеливым — это может быть первое знакомство пользователя с системой.
"""


# ── Построение блока устройств ───────────────────────────────────────────

def build_devices_block(all_devices: dict) -> str:
    """Сформировать текстовый список устройств для промпта."""
    if not all_devices:
        return "Нет подключённых устройств."

    lines = []
    for did, dev in all_devices.items():
        info = dev.get("info", {})
        hostname = info.get("hostname", "?")
        os_name = info.get("os", "?")
        os_ver = info.get("os_version", "")
        lines.append(f"- {did}: hostname={hostname}, ОС={os_name} ({os_ver})")
    return "\n".join(lines)


def build_device_profile_block(profile: dict | None) -> str:
    """Сформировать блок профиля устройства для промпта.
    Содержит информацию о железе, путях, пользователе."""
    if not profile:
        return ""

    lines = ["\n## Профиль устройства"]

    if profile.get("username"):
        lines.append(f"Пользователь: {profile['username']}")
    if profile.get("desktop_path"):
        lines.append(f"Рабочий стол: {profile['desktop_path']}")
    if profile.get("cpu"):
        lines.append(f"Процессор: {profile['cpu']}")
    if profile.get("gpu"):
        lines.append(f"Видеокарта: {profile['gpu']}")
    if profile.get("ram_gb"):
        lines.append(f"Оперативная память: {profile['ram_gb']} ГБ")

    disks = profile.get("disks")
    if disks and isinstance(disks, list):
        disk_lines = []
        for d in disks:
            drive = d.get("drive", "?")
            total = d.get("total_gb", 0)
            free = d.get("free_gb", 0)
            disk_lines.append(f"{drive} {total} ГБ всего, {free} ГБ свободно")
        lines.append(f"Диски: {'; '.join(disk_lines)}")

    # Вернуть пустую строку если только заголовок
    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


# ── Построение истории чата для LLM ──────────────────────────────────────

# Маркеры онбординговых ответов (фильтруем из истории, когда устройства уже подключены)
ONBOARDING_MARKERS = [
    "нет подключённых устройств",
    "нет подключенных устройств",
    "подключить устройство",
    "запустить agent.exe",
    "скачать agent",
    "список доступных устройств пуст",
]


def _is_onboarding_message(content: str) -> bool:
    """Проверить, является ли сообщение онбординговым."""
    lower = content.lower()
    return sum(1 for m in ONBOARDING_MARKERS if m in lower) >= 2


def build_chat_messages(chat_history: list[dict], filter_onboarding: bool = False) -> list[dict]:
    """
    Конвертировать историю чата в формат messages для API.
    Только role='user' и role='assistant', без tool-вызовов из прошлых сессий.
    Если filter_onboarding=True, пропускает онбординговые ответы и вопросы к ним.
    """
    messages = []
    skip_next_user = False
    for msg in chat_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content or role not in ("user", "assistant"):
            continue
        if filter_onboarding and role == "assistant" and _is_onboarding_message(content):
            # Пропустить этот ответ и предыдущее сообщение user (если есть)
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            continue
        messages.append({"role": role, "content": content})
    return messages


# ── Основная логика ──────────────────────────────────────────────────────

async def process_nl_command(
    user_message: str,
    device_id: str,
    device_info: dict,
    all_devices: dict,
    send_command_fn,
    get_file_link_fn,
    chat_history: list[dict] | None = None,
    user_id: int = None,
    chat_id: int = None,
    device_profile: dict | None = None,
    modes: dict | None = None,
    poll_task_id: str | None = None,
) -> dict:
    """
    Обработка команды на естественном языке.

    Args:
        user_message: текст пользователя
        device_id: ID текущего выбранного устройства
        device_info: информация о текущем устройстве
        all_devices: словарь всех подключённых устройств пользователя
        send_command_fn: async fn(device_id, action, params) -> result
        get_file_link_fn: fn(device_id, file_path) -> url_string
        chat_history: история сообщений чата (для памяти)
        user_id: ID пользователя (для записи training data)
        chat_id: ID чата (для записи training data)

    Returns:
        {"answer": str, "commands": [...], "training_context": {...}}
    """
    cfg = load_llm_config()

    # Собрать промпт с информацией обо всех устройствах
    devices_block = build_devices_block(all_devices)
    os_info = device_info.get("os", "Windows")
    hostname = device_info.get("hostname", "unknown")
    os_version = device_info.get("os_version", "")

    profile_block = build_device_profile_block(device_profile)

    # Выбираем OS-специфичные правила по ОС текущего устройства
    os_lower = (os_info or "").lower()
    if "linux" in os_lower:
        os_rules = LINUX_RULES
    else:
        # Windows по умолчанию (включая Windows Server и прочие)
        os_rules = WINDOWS_RULES

    current_datetime_msk = _current_datetime_msk()

    system_msg = SYSTEM_PROMPT_TEMPLATE.format(
        devices_block=devices_block,
        current_device_id=device_id,
        current_hostname=hostname,
        current_os=os_info,
        current_os_version=os_version,
        device_profile_block=profile_block,
        os_rules=os_rules,
        current_datetime_msk=current_datetime_msk,
    )

    # Выбранные пользователем режимы (кнопка "+" в UI)
    modes = modes or {}
    pipeline_forced = bool(modes.get("pipeline"))
    autonomous = bool(modes.get("autonomous"))
    if pipeline_forced or autonomous:
        extra = []
        if pipeline_forced:
            extra.append(
                "КРИТИЧЕСКИ ВАЖНО: режим конвейера АКТИВЕН. Твоё ПЕРВОЕ действие — "
                "вызов create_plan с массивом steps. Без create_plan ты нарушишь контракт. "
                "НЕ выполняй execute_cmd/write_content/web_search до create_plan. "
                "После каждого шага вызывай mark_step."
            )
        if autonomous:
            extra.append(
                "АВТОНОМНЫЙ РЕЖИМ: Пользователь дал согласие на выполнение без дополнительных "
                "подтверждений. Действуй самостоятельно, не спрашивай перед каждой командой. "
                "Запрещённые системные команды всё равно не выполняй."
            )
        system_msg = system_msg + "\n\n## Активные режимы\n" + "\n".join(extra)

    # Формируем messages: system + история чата (без текущего сообщения) + текущее
    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        # История уже содержит текущее сообщение user (оно было сохранено до вызова)
        # Берём все сообщения кроме последнего, фильтруя онбординговые ответы
        history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    commands_log = []
    # Трекинг task_id для конвейера (если LLM вызовет create_plan)
    created_task_ids: list[int] = []

    model = _pick_model(cfg, modes)
    base_model = cfg.get("model", "deepseek-chat")
    print(f"[llm] выбрана модель: {model} (base={base_model}, pipeline={pipeline_forced}, autonomous={autonomous})")

    _timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=_timeout) as client:
        for iteration in range(MAX_ITERATIONS):
            _set_current_step(poll_task_id, "ИРУ думает...")
            print(f"[llm] iteration {iteration+1}/{MAX_ITERATIONS}, messages={len(messages)}")
            try:
                # Формируем параметры запроса; deepseek-reasoner не поддерживает
                # temperature, top_p, presence_penalty, frequency_penalty, response_format
                request_json = {
                    "model": model,
                    "messages": messages,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "max_tokens": cfg.get("max_tokens", 4096),
                }
                if model == base_model:
                    request_json["temperature"] = cfg.get("temperature", 0.0)
                resp = await client.post(
                    f"{cfg['base_url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cfg['api_key']}",
                        "Content-Type": "application/json",
                    },
                    json=request_json,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"[llm] HTTP error: {e.response.status_code} {e.response.text[:500]}")
                raise
            except Exception as e:
                print(f"[llm] request error: {type(e).__name__}: {e}")
                raise

            data = resp.json()
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason", "?")
            assistant_msg = choice["message"]
            content_preview = (assistant_msg.get("content") or "")[:200]
            tool_calls = assistant_msg.get("tool_calls")
            print(f"[llm] response: finish_reason={finish_reason}, "
                  f"has_content={'yes' if content_preview else 'no'}, "
                  f"tool_calls={len(tool_calls) if tool_calls else 0}, "
                  f"content_preview={content_preview[:100]!r}")

            # ── Обработка обрезанного ответа (finish_reason=length) ──
            if finish_reason == "length":
                print(f"[llm] WARNING: response truncated (finish_reason=length)")
                if tool_calls:
                    # tool_call JSON скорее всего обрезан — повторяем
                    # с укороченным контекстом (убираем tool results старше 2 итераций)
                    if iteration < MAX_ITERATIONS - 1:
                        # Попробуем ещё раз: добавим подсказку и продолжим
                        messages.append({
                            "role": "user",
                            "content": "Предыдущий ответ был обрезан. Используй более короткие команды. Попробуй снова.",
                        })
                        print(f"[llm] retrying after truncation (iteration {iteration+1})")
                        continue
                    else:
                        # Последняя итерация — вернуть ошибку
                        return {
                            "answer": "Не удалось выполнить задачу: ответ ИИ слишком длинный и был обрезан. Попробуй сформулировать задачу проще или разбить на несколько шагов.",
                            "commands": commands_log,
                            "tasks": _collect_tasks(created_task_ids),
                            "training_context": {
                                "os": device_info.get("os", ""),
                                "hostname": device_info.get("hostname", ""),
                                "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
                            },
                        }
                else:
                    # Текстовый ответ обрезан — предупредим пользователя
                    truncated_text = assistant_msg.get("content", "") or ""
                    return {
                        "answer": truncated_text + "\n\n[Ответ был обрезан из-за ограничения длины. Попробуй задать более конкретный вопрос.]",
                        "commands": commands_log,
                        "tasks": _collect_tasks(created_task_ids),
                        "training_context": {
                            "os": device_info.get("os", ""),
                            "hostname": device_info.get("hostname", ""),
                            "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
                        },
                    }

            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                # Формируем контекст для записи тренировочных данных
                training_context = {
                    "os": device_info.get("os", ""),
                    "hostname": device_info.get("hostname", ""),
                    "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
                }
                return {
                    "answer": assistant_msg.get("content", "Готово."),
                    "commands": commands_log,
                    "tasks": _collect_tasks(created_task_ids),
                    "training_context": training_context,
                }

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as e:
                    print(f"[llm] BAD JSON in tool args: {e}, raw={tc['function']['arguments'][:300]}")
                    # Битый JSON — сообщить LLM и продолжить
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps({"error": f"Ошибка парсинга аргументов: {e}. Используй более короткую команду."}, ensure_ascii=False),
                    })
                    continue

                # Определить целевое устройство
                target_device = fn_args.pop("device_id", None) or device_id
                print(f"[llm] tool_call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:300]}) -> device={target_device}")

                # ── Live-прогресс: текст для UI ──
                if fn_name == "create_plan":
                    _set_current_step(poll_task_id, "Планирую шаги...")
                elif fn_name == "mark_step":
                    step_num = fn_args.get("idx", "?")
                    step_title = fn_args.get("summary") or ""
                    _set_current_step(poll_task_id, f"Шаг {step_num}: {step_title[:60]}")
                elif fn_name == "web_search":
                    q = (fn_args.get("query") or "").strip()[:80]
                    _set_current_step(poll_task_id, f"Ищу в интернете: {q}")
                elif fn_name == "write_content":
                    path = fn_args.get("path") or fn_args.get("filename", "")
                    name = path.split("\\")[-1].split("/")[-1][:60]
                    _set_current_step(poll_task_id, f"Создаю файл {name}")
                elif fn_name == "execute_cmd":
                    _set_current_step(poll_task_id, "Выполняю команду на устройстве")
                elif fn_name == "get_file_link":
                    _set_current_step(poll_task_id, "Формирую ссылку на файл")

                if fn_name == "execute_cmd":
                    try:
                        tool_result = await send_command_fn(
                            target_device, "execute_cmd", fn_args,
                        )
                        print(f"[llm] cmd result: returncode={tool_result.get('returncode')}, "
                              f"stdout={tool_result.get('stdout', '')[:100]!r}, "
                              f"stderr={tool_result.get('stderr', '')[:100]!r}")
                    except Exception as e:
                        err_str = str(e)
                        print(f"[llm] cmd EXCEPTION: {type(e).__name__}: {err_str[:200]}")
                        if "CONFIRM_REQUIRED" in err_str:
                            raise ConfirmationRequired(
                                command=fn_args.get("command", ""),
                                device_id=target_device,
                                params=fn_args,
                                answer=f"Команда требует подтверждения",
                                commands_log=commands_log,
                            )
                        tool_result = {"error": err_str}

                    commands_log.append({
                        "command": fn_args.get("command", ""),
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                elif fn_name == "create_plan":
                    print(f"[llm] create_plan called: user_id={user_id}, chat_id={chat_id}, args={str(fn_args)[:200]}")
                    try:
                        goal = str(fn_args.get("goal", "")).strip()
                        steps = fn_args.get("steps") or []
                        if not user_id:
                            tool_result = {"error": "внутренняя ошибка: user_id не передан"}
                        elif not goal or not isinstance(steps, list) or not steps:
                            tool_result = {"error": "goal и непустой список steps обязательны"}
                        elif len(steps) > 50:
                            tool_result = {"error": "слишком много шагов (макс 50)"}
                        else:
                            task_id = db.create_task(
                                user_id=user_id,
                                chat_id=chat_id,
                                device_id=target_device,
                                goal=goal,
                                steps=[str(s) for s in steps],
                            )
                            created_task_ids.append(task_id)
                            print(f"[llm] create_plan OK: task_id={task_id}, steps={len(steps)}")
                            tool_result = {
                                "task_id": task_id,
                                "goal": goal,
                                "steps_count": len(steps),
                                "hint": "Теперь выполняй шаги по очереди (idx=0,1,2,...), после каждого вызывай mark_step(task_id, idx, status='done', summary).",
                            }
                    except Exception as e:
                        print(f"[llm] create_plan EXCEPTION: {type(e).__name__}: {e}")
                        import traceback; traceback.print_exc()
                        tool_result = {"error": str(e)}

                elif fn_name == "mark_step":
                    try:
                        task_id_v = int(fn_args.get("task_id", 0))
                        idx_v = int(fn_args.get("idx", 0))
                        status_v = str(fn_args.get("status", "done"))
                        summary_v = fn_args.get("summary")
                        if status_v not in ("running", "done", "failed", "skipped"):
                            tool_result = {"error": f"недопустимый status: {status_v}"}
                        else:
                            ok = db.update_step(task_id_v, idx_v, status_v,
                                                summary=str(summary_v) if summary_v else None)
                            # Если это последний шаг и все done — закрыть задачу
                            if ok and status_v in ("done", "failed"):
                                task = db.get_task(task_id_v)
                                if task:
                                    statuses = [s["status"] for s in task["steps"]]
                                    if all(s in ("done", "skipped") for s in statuses):
                                        db.finish_task(task_id_v, "completed")
                                    elif any(s == "failed" for s in statuses) and \
                                         not any(s == "pending" for s in statuses):
                                        db.finish_task(task_id_v, "failed")
                            tool_result = {
                                "task_id": task_id_v,
                                "idx": idx_v,
                                "status": status_v,
                                "updated": ok,
                            }
                    except Exception as e:
                        print(f"[llm] mark_step EXCEPTION: {type(e).__name__}: {e}")
                        tool_result = {"error": str(e)}

                elif fn_name == "write_content":
                    try:
                        tool_result = await send_command_fn(
                            target_device, "write_content", fn_args,
                        )
                        print(f"[llm] write_content result: {str(tool_result)[:150]}")
                    except Exception as e:
                        err_str = str(e)
                        print(f"[llm] write_content EXCEPTION: {type(e).__name__}: {err_str[:200]}")
                        if "CONFIRM_REQUIRED" in err_str:
                            raise ConfirmationRequired(
                                command=f"write_content: {fn_args.get('path', '')}",
                                device_id=target_device,
                                params=fn_args,
                                answer="Запись в файл требует подтверждения",
                                commands_log=commands_log,
                            )
                        tool_result = {"error": err_str}

                    preview = fn_args.get("content", "")[:60]
                    mode = "append" if fn_args.get("append") else "write"
                    commands_log.append({
                        "command": f"[{mode}] {fn_args.get('path', '')} | {preview}...",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                elif fn_name == "get_file_link":
                    try:
                        file_path = fn_args["file_path"]
                        url = get_file_link_fn(target_device, file_path)
                        tool_result = {"url": url, "file_path": file_path}
                    except Exception as e:
                        tool_result = {"error": str(e)}

                    commands_log.append({
                        "command": f"[скачать] {fn_args.get('file_path', '')}",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                elif fn_name == "web_search":
                    tavily_key = cfg.get("tavily_api_key")
                    if not tavily_key:
                        tool_result = {"error": "tavily_api_key не настроен в llm_config.json на сервере"}
                    else:
                        query = fn_args.get("query", "").strip()
                        max_results = min(int(fn_args.get("max_results", 5) or 5), 10)
                        if not query:
                            tool_result = {"error": "Пустой запрос"}
                        else:
                            try:
                                async with httpx.AsyncClient(timeout=20.0) as tavily_client:
                                    tavily_resp = await tavily_client.post(
                                        "https://api.tavily.com/search",
                                        json={
                                            "api_key": tavily_key,
                                            "query": query,
                                            "max_results": max_results,
                                            "search_depth": "basic",
                                            "include_answer": True,
                                        },
                                    )
                                    tavily_resp.raise_for_status()
                                    tavily_data = tavily_resp.json()
                                compact = {
                                    "answer": tavily_data.get("answer"),
                                    "results": [
                                        {
                                            "title": r.get("title"),
                                            "url": r.get("url"),
                                            "content": (r.get("content") or "")[:800],
                                        }
                                        for r in (tavily_data.get("results") or [])[:max_results]
                                    ],
                                }
                                tool_result = compact
                            except Exception as e:
                                tool_result = {"error": f"Ошибка Tavily: {e}"}

                    commands_log.append({
                        "command": f"[web_search] {fn_args.get('query', '')[:80]}",
                        "device_id": target_device,
                        "result": tool_result if not isinstance(tool_result, dict) or "error" in tool_result else {"ok": True},
                        "iteration": iteration + 1,
                    })

                else:
                    tool_result = {"error": f"Неизвестная функция: {fn_name}"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
                })

    # Формируем контекст для записи тренировочных данных (предел итераций)
    # Помечаем висячие задачи как failed, если LLM вышел из цикла по MAX_ITERATIONS
    for tid in created_task_ids:
        try:
            task = db.get_task(tid)
            if task and task["status"] == "running":
                db.finish_task(tid, "failed")
        except Exception:
            pass

    training_context = {
        "os": device_info.get("os", ""),
        "hostname": device_info.get("hostname", ""),
        "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
    }
    tasks_summary = _collect_tasks(created_task_ids)
    return {
        "answer": "Достигнут лимит итераций. Последние результаты в логе.",
        "commands": commands_log,
        "training_context": training_context,
        "tasks": tasks_summary,
    }


# ── Режим без устройств (onboarding) ────────────────────────────

# Текст инструкции для пользователя (чистый текст, без HTML)
INSTRUCTION_TEXT = """\
Что понадобится:
- Компьютер на Windows 10/11
- Токен доступа (получить у администратора)
- Файл agent.exe

Шаг 1: Скачать agent.exe.

Шаг 2: Запустить agent.exe двойным кликом.
При первом запуске откроется окно — вставьте туда токен доступа и нажмите "Подключиться".
Токен сохранится автоматически — при следующих запусках вводить не нужно.

После подключения устройство появится в интерфейсе автоматически.
"""


async def process_onboarding_message(
    user_message: str,
    chat_history: list[dict] | None = None,
) -> dict:
    """
    Режим без устройств: простой чат с LLM без tools.
    Помогает пользователю подключить первое устройство.
    """
    cfg = load_llm_config()

    current_datetime_msk = _current_datetime_msk()

    system_msg = ONBOARDING_PROMPT.format(
        instruction_text=INSTRUCTION_TEXT,
        current_datetime_msk=current_datetime_msk,
    )

    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        history_msgs = build_chat_messages(chat_history[:-1])
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        resp = await client.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": cfg.get("max_tokens", 4096),
                "temperature": cfg.get("temperature", 0.0),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    answer = data["choices"][0]["message"].get("content", "")
    return {"answer": answer, "commands": []}

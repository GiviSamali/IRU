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
import logging
import os
import re
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


def strip_markdown(text: str) -> str:
    """Убрать markdown-разметку из текста LLM перед отправкой пользователю."""
    if not text:
        return text
    text = re.sub(r'\*\*([^*]+)\*\*', lambda m: m.group(1).upper(), text)
    text = re.sub(r'(?<![*\w])\*([^*\n]+)\*(?!\w)', r'\1', text)
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)[-*]\s+', r'\1— ', text, flags=re.MULTILINE)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    return text


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


def _push_tasks_view(poll_task_id: str | None, task_ids: list[int]) -> None:
    """Обновить task['tasks'] для live-отображения шагов плана в UI."""
    if not poll_task_id or not task_ids:
        return
    try:
        from main import tasks
        t = tasks.get(poll_task_id)
        if t:
            t["tasks"] = _collect_tasks(task_ids)
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


logger = logging.getLogger("iru.classify")

# ── Быстрые слова-триггеры для PLAN ──────────────────────────────────────
_PLAN_KEYWORDS = ("план", "пошагово", "по шагам")

_CLASSIFY_SYSTEM = (
    "Ты классификатор задач. Определи: "
    "PLAN (многошаговая: установка ПО, создание проектов, настройка сред, "
    "цепочка из 3+ действий) или SIMPLE (одна команда, текстовый ответ, "
    "один файл, простой вопрос). "
    "Верни РОВНО одну строку: 'PLAN: краткое описание плана' или 'SIMPLE'. "
    "Никаких объяснений."
)


async def classify_task_complexity(message: str) -> tuple[str, str]:
    """Лёгкий LLM-вызов для классификации задачи: PLAN или SIMPLE.

    Возвращает (kind, plan_desc):
      - ("PLAN", "описание")  — сложная задача
      - ("SIMPLE", "")        — простая задача
    """
    # Fast-path: ключевые слова → сразу PLAN без LLM
    msg_lower = message.lower()
    for kw in _PLAN_KEYWORDS:
        if kw in msg_lower:
            logger.info("[classify] fast-path keyword=%r → PLAN, message=%r", kw, message[:100])
            return ("PLAN", "Запрошен пошаговый план")

    cfg = load_llm_config()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_SYSTEM},
                        {"role": "user", "content": message},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        answer = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:
        logger.warning("[classify] LLM error, fallback to SIMPLE: %s", exc)
        return ("SIMPLE", "")

    if answer.upper().startswith("PLAN:"):
        plan_desc = answer[5:].strip()
        logger.info("[classify] kind=PLAN plan_desc=%r message=%r", plan_desc[:80], message[:100])
        return ("PLAN", plan_desc)

    logger.info("[classify] kind=SIMPLE message=%r", message[:100])
    return ("SIMPLE", "")


# ── Системный промпт (шаблон) ───────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
Ты — ИРУ (Интеллектуальный Режим Управления), ИИ-ассистент для управления \
компьютерами пользователя через командную строку.

КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ ОТВЕТА:
Отвечай пользователю только чистым текстом без Markdown-разметки.
Запрещено использовать: звёздочки (*, **), решётки (#, ##, ###), обратные кавычки (`, ```),
дефисы в начале строк для списков (-, *), подчёркивания (__, _), квадратные скобки для ссылок ([текст](url)).
Для выделения важного — пиши СЛОВО КАПСОМ или просто выдели логически.
Для списков — нумеруй «1.», «2.», «3.» в начале строки, либо перечисляй через точку с запятой в одном абзаце.
Это правило строже любых привычек Markdown. Оно не имеет исключений.

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
{device_memory_block}
## Доступные инструменты

### 1. execute_cmd
Выполнить команду на устройстве.
- command (string, обязательно): команда для выполнения
- timeout (integer, по умолчанию 30): таймаут в секундах
- shell (string, по умолчанию "auto"): "powershell", "cmd" или "bash"
- device_id (string, опционально): ID устройства. Если не указан — \
выполняется на текущем устройстве.
- long_running (boolean, по умолчанию false): установи true для запуска \
GUI-приложений (PyQt5, tkinter, WinForms, Electron, браузеры) и фоновых \
процессов, которые не завершаются сами. Команда запустится, подождёт 3 сек \
и вернёт успех. НЕ указывай timeout при long_running=true.

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
10. Для путей к рабочему столу и папкам пользователя — ВСЕГДА используй путь из \
профиля устройства (раздел "Профиль устройства" выше), а не переменные окружения. \
Если в профиле указан конкретный путь к рабочему столу — используй именно его.
11. Для работы с приложениями используй программные интерфейсы, а не эмуляцию клавиш. \
Философия: научить машину быть машиной — никакой эмуляции пользователя.
12. ЗАПИСЬ ТЕКСТА В ФАЙЛЫ: для любого текста длиннее 200 символов или с переносами строк \
ИСПОЛЬЗУЙ инструмент write_content, а НЕ execute_cmd с Set-Content/echo/heredoc. \
write_content не требует экранирования кавычек/переносов и работает одинаково на Windows и Linux. \
Если текст очень большой и не помещается в один ответ — первый вызов с append=false, \
дальше append=true для каждой следующей части.
13. КОНВЕЙЕР (многошаговые задачи): если задача требует 3+ разных действий или чётко \
делится на шаги ("собери данные и сделай отчёт", "установи X, сконфигурируй, проверь") — \
СНАЧАЛА вызови create_plan с чётким списком шагов (3-10 штук, каждый в одну строку в формате \
глагол+деталь). Затем выполняй шаги по очереди через execute_cmd/write_content, и после \
каждого закрытого шага вызывай mark_step(task_id, idx, status="done"|"failed", summary). \
Простые задачи (1-2 действия) делай без плана — не засоряй UI.
14. Для создания текстовых файлов (.txt, .md) ВСЕГДА используй инструмент write_content. \
ЗАПРЕЩЕНО создавать текстовые файлы через PowerShell с New-Object -ComObject Word.Application, \
Word.Selection.TypeText, Word.Selection.TypeParagraph. Эти методы приводят к падению агента. \
Для больших текстов — только write_content.
15. Для поиска информации в интернете используй ТОЛЬКО инструмент web_search. ЗАПРЕЩЕНО \
использовать Invoke-WebRequest, curl, wget для поиска (duckduckgo.com, google.com/search, \
bing.com/search и т.п.) — это не работает и возвращает мусор. Если нет актуальной информации — \
вызывай web_search.

РАБОТА С РУССКИМ ТЕКСТОМ В ФАЙЛАХ:
Когда сохраняешь русский текст в файлы (.txt, .csv, .xlsx, .docx и т. д.) — пиши его ИМЕННО РУССКИМИ БУКВАМИ (кириллицей), никогда не транслитерируй.
Для .xlsx используй Python с openpyxl: именно openpyxl корректно работает с UTF-8 и кириллицей.
Пример правильной записи xlsx:
  from openpyxl import Workbook
  wb = Workbook()
  ws = wb.active
  ws["A1"] = "Привет"   # именно так, кириллицей
  wb.save("output.xlsx")
Для .csv — всегда указывай encoding="utf-8-sig" при записи.
Для .txt — всегда encoding="utf-8".
В PowerShell при записи файлов с русским текстом используй параметр -Encoding UTF8.
Запрещено: транслитерировать кириллицу в латиницу ("Privet", "Kak dela") — это считается ошибкой.

ПРОАКТИВНАЯ ПАМЯТЬ:
Если при выполнении задачи ты обнаружил полезный факт об устройстве или пользователе \
(предпочтение, особенность конфигурации, ограничение, нестандартный путь), который стоит \
запомнить для будущих запросов — добавь в КОНЕЦ своего финального ответа маркер:
[[SUGGEST_REMEMBER: текст факта | категория]]
Например: [[SUGGEST_REMEMBER: На этом ПК Python установлен в D:\\Python311 | config]]
Категории: preference, config, warning, layout, software. Не ставь маркер для тривиальных фактов.

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
                    },
                    "long_running": {
                        "type": "boolean",
                        "description": "Установи true для GUI-приложений (PyQt5, tkinter, WinForms) и процессов, которые не завершаются сами. Команда будет запущена, а через 3 секунды вернётся успех без ожидания завершения.",
                        "default": False
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
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Запомнить важный факт об этом устройстве или пользователе. Использовать для предпочтений, ограничений, особенностей раскладки, тонкостей конфигурации, которые нужно помнить между чатами. Не использовать для результатов команд — они запоминаются автоматически.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Текст факта для запоминания"
                    },
                    "category": {
                        "type": "string",
                        "description": "Категория факта (например preference, layout, warning). Необязательно."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forget_fact",
            "description": "Удалить ранее сохранённый факт по его id. Id фактов видны в блоке 'Память об этом устройстве', который есть в системном промпте. Если id не найден для текущего устройства, операция игнорируется.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "integer",
                        "description": "ID факта для удаления"
                    }
                },
                "required": ["fact_id"]
            }
        }
    }
]

WORKER_TOOL_NAMES = {
    "execute_cmd",
    "write_content",
    "get_file_link",
    "web_search",
    "remember_fact",
    "forget_fact",
}
WORKER_TOOLS = [
    tool for tool in TOOLS
    if tool["function"]["name"] in WORKER_TOOL_NAMES
]

MAX_ITERATIONS = 20
PIPELINE_WORKER_MAX_ITERATIONS = 10
PIPELINE_MAX_STEPS = 10


def _pick_model(cfg: dict, modes: dict | None) -> str:
    """Выбрать модель LLM: deepseek-reasoner для сложных режимов, deepseek-chat иначе."""
    base = cfg.get("model", "deepseek-chat")
    reasoner = cfg.get("model_reasoner", "deepseek-reasoner")
    is_complex = bool(modes) and (modes.get("pipeline") or modes.get("autonomous"))
    return reasoner if is_complex else base


async def _chat_completion_request(
    client: httpx.AsyncClient,
    cfg: dict,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Единая обёртка для вызова chat/completions с ретраями."""
    request_json = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or cfg.get("max_tokens", 4096),
    }
    if tools is not None:
        request_json["tools"] = tools
        request_json["tool_choice"] = "auto"

    base_model = cfg.get("model", "deepseek-chat")
    if model == base_model:
        request_json["temperature"] = cfg.get("temperature", 0.0)

    resp = None
    for _attempt in range(2):
        try:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=request_json,
            )
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError as _he:
            if _he.response.status_code >= 500 and _attempt == 0:
                print(f"[llm] 5xx retry: {_he.response.status_code}")
                await asyncio.sleep(2)
                continue
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as _ne:
            if _attempt == 0:
                print(f"[llm] network retry: {type(_ne).__name__}")
                await asyncio.sleep(2)
                continue
            raise

    return resp.json()


def _extract_json_payload(text: str):
    """Достать JSON-объект или массив из ответа модели."""
    if not text:
        return None

    raw = text.strip()
    candidates = [raw]

    start_obj = raw.find("{")
    end_obj = raw.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        candidates.append(raw[start_obj:end_obj + 1])

    start_arr = raw.find("[")
    end_arr = raw.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        candidates.append(raw[start_arr:end_arr + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _normalize_pipeline_plan(raw_plan, fallback_goal: str, default_device_id: str) -> dict:
    """Нормализовать план оркестратора к единому виду."""
    goal = fallback_goal.strip() or "Выполнить задачу"
    steps_raw = []
    if isinstance(raw_plan, dict):
        goal = str(raw_plan.get("goal") or raw_plan.get("title") or goal).strip() or goal
        steps_raw = raw_plan.get("steps") or []
    elif isinstance(raw_plan, list):
        steps_raw = raw_plan

    steps = []
    for idx, item in enumerate(steps_raw[:PIPELINE_MAX_STEPS]):
        if isinstance(item, str):
            title = item.strip()
            instruction = title
            success_criteria = ""
            step_device_id = default_device_id
        elif isinstance(item, dict):
            title = str(
                item.get("title")
                or item.get("step")
                or item.get("name")
                or item.get("instruction")
                or f"Шаг {idx + 1}"
            ).strip()
            instruction = str(
                item.get("instruction")
                or item.get("details")
                or item.get("objective")
                or item.get("task")
                or title
            ).strip()
            success_criteria = str(
                item.get("success_criteria")
                or item.get("success")
                or item.get("done_when")
                or ""
            ).strip()
            step_device_id = str(item.get("device_id") or default_device_id).strip() or default_device_id
        else:
            continue

        if not title:
            continue
        if not instruction:
            instruction = title

        steps.append({
            "title": title[:160],
            "instruction": instruction[:1400],
            "success_criteria": success_criteria[:400],
            "device_id": step_device_id,
        })

    if not steps:
        steps = [{
            "title": goal[:160],
            "instruction": fallback_goal.strip() or goal,
            "success_criteria": "",
            "device_id": default_device_id,
        }]

    return {
        "goal": goal[:200],
        "steps": steps,
    }


def _pipeline_plan_prompt(shared: dict, user_message: str) -> str:
    """Промпт для оркестратора: разбить задачу на subagent-шаги."""
    return f"""\
Ты — ОРКЕСТРАТОР конвейерного режима ИРУ.

Твоя роль: НЕ выполнять команды самостоятельно, а разбить общий запрос на понятные subagent-шаги.
Каждый шаг потом пойдёт отдельному LLM-исполнителю. Поэтому шаги должны быть:
1. Непересекающимися.
2. Последовательными.
3. Достаточно конкретными, чтобы исполнитель мог сделать шаг без нового планирования.
4. В количестве от 2 до {PIPELINE_MAX_STEPS}, если только задача не совсем точечная.

Верни ТОЛЬКО JSON без Markdown и без пояснений в таком формате:
{{
  "goal": "краткая цель",
  "steps": [
    {{
      "title": "короткое название шага",
      "instruction": "подробное задание для subagent-исполнителя",
      "success_criteria": "как понять, что шаг завершён",
      "device_id": "ID устройства, если шаг лучше делать не на текущем устройстве"
    }}
  ]
}}

Поле device_id можно опускать, если подходит текущее устройство.
Не создавай лишних микро-шагов. Не используй маркеры [[SUGGEST_PLAN]].

Текущая дата и время: {shared["current_datetime_msk"]}.

Подключённые устройства:
{shared["devices_block"]}

Текущее устройство:
ID: {shared["current_device_id"]}
Hostname: {shared["current_hostname"]}
ОС: {shared["current_os"]} ({shared["current_os_version"]})

Профиль устройства:
{shared["device_profile_block"] or "Нет расширенного профиля."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Запрос пользователя:
{user_message}
"""


def _pipeline_worker_prompt(
    shared: dict,
    overall_goal: str,
    step: dict,
    completed_steps: list[dict],
) -> str:
    """Промпт для subagent-исполнителя одного шага."""
    completed_block = "Нет завершённых шагов."
    if completed_steps:
        completed_block = "\n".join(
            f"- {item['title']}: {item['summary']}"
            for item in completed_steps[-6:]
        )

    step_device_id = step.get("device_id") or shared["current_device_id"]
    return f"""\
Ты — SUBAGENT-ИСПОЛНИТЕЛЬ внутри Pipeline Mode ИРУ.

Ты выполняешь ТОЛЬКО ОДИН назначенный шаг. Ты не главный ассистент и не оркестратор.
Твоя задача: довести текущий шаг до результата с помощью инструментов и затем коротко отчитаться.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. Не создавай новый план.
2. Не используй create_plan и mark_step — их нет в твоих инструментах.
3. Действуй только в рамках текущего шага.
4. Если шаг завершён — верни короткий итог простым текстом без Markdown.
5. Если шаг не удаётся — верни краткое описание проблемы и на чём остановился.
6. Для длинных текстов и файлов используй write_content.
7. Для актуальной информации используй только web_search.

Общая цель:
{overall_goal}

Текущий шаг:
Название: {step.get("title", "")}
Задание: {step.get("instruction", "")}
Критерий успеха: {step.get("success_criteria", "Не задан явно")}
Предпочтительное устройство: {step_device_id}

Что уже сделано:
{completed_block}

Подключённые устройства:
{shared["devices_block"]}

Текущее устройство:
ID: {shared["current_device_id"]}
Hostname: {shared["current_hostname"]}
ОС: {shared["current_os"]} ({shared["current_os_version"]})

Профиль устройства:
{shared["device_profile_block"] or "Нет расширенного профиля."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Текущая дата и время: {shared["current_datetime_msk"]}.
"""


def _pipeline_summary_prompt() -> str:
    """Финальный промпт оркестратора для сборки общего ответа."""
    return """\
Ты — ОРКЕСТРАТОР Pipeline Mode ИРУ.

Тебе дали результат работы subagent-исполнителей по шагам. Сформируй финальный ответ пользователю:
1. Кратко скажи, что сделано.
2. Если выполнение остановилось — честно укажи на каком шаге и почему.
3. Если есть полезный итоговый артефакт или ссылка на скачивание — упомяни это явно.
4. Пиши только чистым текстом без Markdown.
5. Если стоит запомнить важный факт о конфигурации или предпочтении пользователя — можешь в САМОМ КОНЦЕ добавить маркер:
[[SUGGEST_REMEMBER: текст факта | категория]]
Категории: preference, config, warning, layout, software.
"""


def _build_pipeline_shared_context(
    device_id: str,
    device_info: dict,
    all_devices: dict,
    device_profile: dict | None,
    machine_guid: str | None,
    mem_user_id: str | None,
) -> dict:
    """Контекст окружения для оркестратора и subagent-исполнителей."""
    os_info = device_info.get("os", "Windows")
    os_lower = (os_info or "").lower()
    return {
        "devices_block": build_devices_block(all_devices),
        "current_device_id": device_id,
        "current_hostname": device_info.get("hostname", "unknown"),
        "current_os": os_info,
        "current_os_version": device_info.get("os_version", ""),
        "device_profile_block": build_device_profile_block(device_profile),
        "device_memory_block": build_memory_block(machine_guid, mem_user_id),
        "os_rules": LINUX_RULES if "linux" in os_lower else WINDOWS_RULES,
        "current_datetime_msk": _current_datetime_msk(),
    }


async def _run_pipeline_worker(
    client: httpx.AsyncClient,
    cfg: dict,
    model: str,
    shared: dict,
    overall_goal: str,
    step: dict,
    completed_steps: list[dict],
    chat_history: list[dict] | None,
    send_command_fn,
    get_file_link_fn,
    machine_guid: str | None,
    mem_user_id: str | None,
    poll_task_id: str | None,
) -> dict:
    """Subagent-исполнитель одного шага pipeline."""
    worker_prompt = _pipeline_worker_prompt(shared, overall_goal, step, completed_steps)
    messages = [{"role": "system", "content": worker_prompt}]
    if chat_history:
        messages.extend(build_chat_messages(chat_history[:-1], filter_onboarding=True)[-6:])
    messages.append({
        "role": "user",
        "content": (
            f"Выполни шаг: {step.get('title', '')}\n"
            f"Инструкция: {step.get('instruction', '')}\n"
            f"Критерий успеха: {step.get('success_criteria', 'не задан')}"
        ),
    })

    commands_log = []
    step_device_id = step.get("device_id") or shared["current_device_id"]

    for iteration in range(PIPELINE_WORKER_MAX_ITERATIONS):
        print(
            f"[pipeline/worker] iteration {iteration + 1}/{PIPELINE_WORKER_MAX_ITERATIONS}, "
            f"step={step.get('title', '')[:60]!r}"
        )
        data = await _chat_completion_request(
            client=client,
            cfg=cfg,
            model=model,
            messages=messages,
            tools=WORKER_TOOLS,
        )
        choice = data["choices"][0]
        assistant_msg = choice["message"]
        finish_reason = choice.get("finish_reason", "?")
        content_preview = (assistant_msg.get("content") or "")[:120]
        tool_calls = assistant_msg.get("tool_calls")
        print(
            f"[pipeline/worker] response: finish_reason={finish_reason}, "
            f"tool_calls={len(tool_calls) if tool_calls else 0}, "
            f"content_preview={content_preview!r}"
        )

        if finish_reason == "length":
            if tool_calls and iteration < PIPELINE_WORKER_MAX_ITERATIONS - 1:
                messages.append({
                    "role": "user",
                    "content": "Предыдущий ответ был обрезан. Продолжи короче и точнее.",
                })
                continue
            if not tool_calls:
                return {
                    "status": "ok",
                    "answer": (assistant_msg.get("content") or "").strip() or "Шаг завершён.",
                    "commands": commands_log,
                }

        messages.append(assistant_msg)

        if not tool_calls:
            return {
                "status": "ok",
                "answer": (assistant_msg.get("content") or "").strip() or "Шаг завершён.",
                "commands": commands_log,
            }

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError as e:
                tool_result = {
                    "error": f"Ошибка парсинга аргументов: {e}. Используй более короткие аргументы."
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })
                continue

            target_device = fn_args.pop("device_id", None) or step_device_id
            print(
                f"[pipeline/worker] tool_call: {fn_name}"
                f"({json.dumps(fn_args, ensure_ascii=False)[:250]}) -> device={target_device}"
            )

            if fn_name == "execute_cmd":
                _set_current_step(poll_task_id, f"Исполняю шаг: {step.get('title', '')[:60]}")
                is_long_running = fn_args.pop("long_running", False)
                try:
                    if is_long_running:
                        fn_args["timeout"] = 5
                        try:
                            tool_result = await send_command_fn(target_device, "execute_cmd", fn_args)
                        except Exception as lr_e:
                            if "Таймаут" in str(lr_e):
                                tool_result = {
                                    "stdout": "Приложение запущено (long_running)",
                                    "stderr": "",
                                    "returncode": 0,
                                    "error": None,
                                }
                            else:
                                raise
                    else:
                        tool_result = await send_command_fn(target_device, "execute_cmd", fn_args)
                except Exception as e:
                    err_str = str(e)
                    if "CONFIRM_REQUIRED" in err_str:
                        raise ConfirmationRequired(
                            command=fn_args.get("command", ""),
                            device_id=target_device,
                            params=fn_args,
                            answer=f"Для шага «{step.get('title', '')}» требуется подтверждение команды",
                            commands_log=commands_log,
                        )
                    tool_result = {"error": err_str}

                commands_log.append({
                    "command": fn_args.get("command", ""),
                    "device_id": target_device,
                    "result": tool_result,
                    "iteration": iteration + 1,
                })
                if machine_guid and "error" not in tool_result:
                    try:
                        db.add_command_memory(
                            machine_guid=machine_guid,
                            device_id=target_device,
                            command=fn_args.get("command", ""),
                            intent=step.get("title"),
                            exit_code=int(tool_result.get("returncode", -1)),
                            stdout=tool_result.get("stdout"),
                            stderr=tool_result.get("stderr"),
                            user_id=mem_user_id,
                        )
                    except Exception:
                        print("[pipeline/worker] Failed to write command memory")

            elif fn_name == "write_content":
                _set_current_step(poll_task_id, f"Создаю файл для шага: {step.get('title', '')[:50]}")
                try:
                    tool_result = await send_command_fn(target_device, "write_content", fn_args)
                except Exception as e:
                    err_str = str(e)
                    if "CONFIRM_REQUIRED" in err_str:
                        raise ConfirmationRequired(
                            command=f"write_content: {fn_args.get('path', '')}",
                            device_id=target_device,
                            params=fn_args,
                            answer=f"Для шага «{step.get('title', '')}» требуется подтверждение записи в файл",
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
                _set_current_step(poll_task_id, f"Формирую ссылку: {step.get('title', '')[:50]}")
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
                _set_current_step(poll_task_id, f"Ищу данные для шага: {step.get('title', '')[:50]}")
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
                            tavily_data = None
                            async with httpx.AsyncClient(timeout=20.0) as tavily_client:
                                for _tavily_attempt in range(2):
                                    try:
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
                                        break
                                    except (httpx.HTTPStatusError, httpx.ConnectError,
                                            httpx.ReadTimeout, httpx.ConnectTimeout) as _te:
                                        is_5xx = isinstance(_te, httpx.HTTPStatusError) and _te.response.status_code >= 500
                                        is_net = isinstance(_te, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout))
                                        if (is_5xx or is_net) and _tavily_attempt == 0:
                                            print(f"[pipeline/worker] tavily retry: {type(_te).__name__}")
                                            await asyncio.sleep(2)
                                            continue
                                        raise
                            if tavily_data is None:
                                tool_result = {"error": "Поиск временно недоступен. Попробуйте позже."}
                            else:
                                tool_result = {
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
                        except Exception as e:
                            tool_result = {"error": f"Поиск временно недоступен: {e}"}

                commands_log.append({
                    "command": f"[web_search] {fn_args.get('query', '')[:80]}",
                    "device_id": target_device,
                    "result": tool_result if not isinstance(tool_result, dict) or "error" in tool_result else {"ok": True},
                    "iteration": iteration + 1,
                })

            elif fn_name == "remember_fact":
                if not mem_user_id:
                    tool_result = {"result": "Не удалось сохранить факт: пользователь не идентифицирован"}
                else:
                    try:
                        fact_id = db.add_user_fact(
                            user_id=mem_user_id,
                            text=fn_args.get("text", ""),
                            category=fn_args.get("category"),
                        )
                        tool_result = {"result": f"Запомнил факт о тебе (id={fact_id})"}
                    except Exception as e:
                        tool_result = {"error": str(e)}

            elif fn_name == "forget_fact":
                if not mem_user_id:
                    tool_result = {"result": "Факт не найден"}
                else:
                    try:
                        ok = db.delete_user_fact(mem_user_id, int(fn_args.get("fact_id", 0)))
                        tool_result = {"result": "Факт удалён" if ok else "Факт не найден"}
                    except Exception as e:
                        tool_result = {"error": str(e)}

            else:
                tool_result = {"error": f"Неизвестная функция: {fn_name}"}

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
            })

    return {
        "status": "error",
        "answer": "Subagent достиг лимита итераций и остановил шаг.",
        "commands": commands_log,
    }


async def _process_pipeline_subagents(
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
    """Pipeline Mode с субагентностью: orchestrator -> step workers -> final synthesis."""
    cfg = load_llm_config()
    modes = modes or {}
    model = _pick_model(cfg, {"pipeline": True, "autonomous": bool(modes.get("autonomous"))})
    machine_guid = (device_profile or {}).get("machine_guid") or None
    mem_user_id = str(user_id) if user_id else (f"anon_{machine_guid}" if machine_guid else None)
    shared = _build_pipeline_shared_context(
        device_id=device_id,
        device_info=device_info,
        all_devices=all_devices,
        device_profile=device_profile,
        machine_guid=machine_guid,
        mem_user_id=mem_user_id,
    )

    _set_current_step(poll_task_id, "Оркестратор строит план...")
    history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)[-8:] if chat_history else []
    plan_messages = [{"role": "system", "content": _pipeline_plan_prompt(shared, user_message)}]
    plan_messages.extend(history_msgs)
    plan_messages.append({"role": "user", "content": user_message})

    step_results = []
    all_commands = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        planner_data = await _chat_completion_request(
            client=client,
            cfg=cfg,
            model=model,
            messages=plan_messages,
            tools=None,
            max_tokens=min(cfg.get("max_tokens", 4096), 2500),
        )
        planner_text = (planner_data["choices"][0]["message"].get("content") or "").strip()
        normalized_plan = _normalize_pipeline_plan(
            _extract_json_payload(planner_text),
            fallback_goal=user_message,
            default_device_id=device_id,
        )

        db_task_id = db.create_task(
            user_id=user_id,
            chat_id=chat_id,
            device_id=device_id,
            goal=normalized_plan["goal"],
            steps=[step["title"] for step in normalized_plan["steps"]],
        )
        created_task_ids = [db_task_id]
        _push_tasks_view(poll_task_id, created_task_ids)

        pipeline_failed = False
        failure_reason = ""
        for idx, step in enumerate(normalized_plan["steps"]):
            db.update_step(db_task_id, idx, "running", summary="Подзадача передана subagent-исполнителю")
            _push_tasks_view(poll_task_id, created_task_ids)
            _set_current_step(
                poll_task_id,
                f"Шаг {idx + 1}/{len(normalized_plan['steps'])}: {step['title'][:80]}"
            )
            try:
                worker_result = await _run_pipeline_worker(
                    client=client,
                    cfg=cfg,
                    model=model,
                    shared=shared,
                    overall_goal=normalized_plan["goal"],
                    step=step,
                    completed_steps=step_results,
                    chat_history=chat_history,
                    send_command_fn=send_command_fn,
                    get_file_link_fn=get_file_link_fn,
                    machine_guid=machine_guid,
                    mem_user_id=mem_user_id,
                    poll_task_id=poll_task_id,
                )
            except ConfirmationRequired:
                raise
            except Exception as e:
                worker_result = {
                    "status": "error",
                    "answer": f"Ошибка subagent-исполнителя: {e}",
                    "commands": [],
                }

            all_commands.extend(worker_result.get("commands", []))
            step_summary = strip_markdown(worker_result.get("answer", "")).strip() or "Шаг завершён."
            urls = [
                cmd.get("result", {}).get("url")
                for cmd in worker_result.get("commands", [])
                if isinstance(cmd.get("result"), dict) and cmd["result"].get("url")
            ]
            if urls:
                step_summary = f"{step_summary} Ссылки: {'; '.join(urls)}"

            step_status = "done" if worker_result.get("status") == "ok" else "failed"
            db.update_step(db_task_id, idx, step_status, summary=step_summary[:500])
            _push_tasks_view(poll_task_id, created_task_ids)

            step_results.append({
                "idx": idx,
                "title": step["title"],
                "instruction": step["instruction"],
                "device_id": step.get("device_id") or device_id,
                "status": step_status,
                "summary": step_summary,
            })

            if step_status != "done":
                pipeline_failed = True
                failure_reason = step_summary
                break

        db.finish_task(db_task_id, "failed" if pipeline_failed else "completed")
        _push_tasks_view(poll_task_id, created_task_ids)
        _set_current_step(poll_task_id, "Оркестратор подводит итоги...")

        summary_payload = {
            "goal": normalized_plan["goal"],
            "pipeline_status": "failed" if pipeline_failed else "completed",
            "failure_reason": failure_reason,
            "steps": step_results,
        }
        summary_messages = [
            {"role": "system", "content": _pipeline_summary_prompt()},
            {"role": "user", "content": json.dumps(summary_payload, ensure_ascii=False, indent=2)},
        ]
        try:
            summary_data = await _chat_completion_request(
                client=client,
                cfg=cfg,
                model=cfg.get("model", "deepseek-chat"),
                messages=summary_messages,
                tools=None,
                max_tokens=min(cfg.get("max_tokens", 4096), 1200),
            )
            final_answer = (summary_data["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            print(f"[pipeline] summary error: {e}")
            final_answer = (
                "План выполнен не полностью." if pipeline_failed else "План выполнен."
            )
            if step_results:
                final_answer += " " + " ".join(
                    f"{s['title']}: {s['summary']}" for s in step_results[-3:]
                )

    final_answer = strip_markdown(final_answer)
    return {
        "answer": final_answer,
        "commands": all_commands,
        "tasks": _collect_tasks(created_task_ids),
        "training_context": {
            "os": device_info.get("os", ""),
            "hostname": device_info.get("hostname", ""),
            "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
        },
    }


# ── Промпт для режима без устройств (помощник по настройке) ─────────────────

ONBOARDING_PROMPT = """\
Ты — ИРУ (Интеллектуальный Режим Управления), ИИ-ассистент для управления \
компьютером через естественный язык.

КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ ОТВЕТА:
Отвечай пользователю только чистым текстом без Markdown-разметки.
Запрещено использовать: звёздочки (*, **), решётки (#, ##, ###), обратные кавычки (`, ```),
дефисы в начале строк для списков (-, *), подчёркивания (__, _), квадратные скобки для ссылок ([текст](url)).
Для выделения важного — пиши СЛОВО КАПСОМ или просто выдели логически.
Для списков — нумеруй «1.», «2.», «3.» в начале строки, либо перечисляй через точку с запятой в одном абзаце.
Это правило строже любых привычек Markdown. Оно не имеет исключений.

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
4. Будь дружелюбным и терпеливым — это может быть первое знакомство пользователя с системой.
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


# ── Построение блока памяти устройства ─────────────────────────────────

MAX_MEMORY_BLOCK = 2048


def build_memory_block(machine_guid: str | None, user_id: str | None = None) -> str:
    """Собрать блок «Память» для промпта (≤2048 символов).
    Факты — из user_memory (по user_id), команды — из device_memory (по machine_guid)."""
    if not machine_guid and not user_id:
        return ""

    # Факты пользователя
    user_facts = []
    if user_id:
        user_facts = db.get_user_facts(user_id)

    # Команды устройства
    commands = []
    if machine_guid:
        commands = db.get_recent_commands(machine_guid, user_id, 20)

    if not user_facts and not commands:
        return ""

    # — Факты обо мне (не обрезаем) —
    facts_lines = []
    if user_facts:
        facts_lines.append("Факты обо мне, пользователе:")
        for f in user_facts:
            cat = f"[{f['category']}] " if f.get("category") else ""
            facts_lines.append(f"- id={f['id']} {cat}{f['fact_text']}")

    # — Команды (сначала 200 символов preview, потом 100 при необходимости) —
    def _cmd_line(c: dict, preview_limit: int) -> str:
        tag = "[OK]" if c["success"] else "[FAIL]"
        intent_part = f" — (intent: {c['intent']})" if c.get("intent") else ""
        if c["success"]:
            out = (c.get("stdout_preview") or "")[:preview_limit]
            out_part = f" — stdout: {out}" if out else ""
        else:
            out = (c.get("stderr_preview") or "")[:preview_limit]
            out_part = f" — stderr: {out}" if out else ""
        return f"- {tag} {c['command']} — exit={c['exit_code']}{intent_part}{out_part}"

    def _assemble(cmd_lines: list[str]) -> str:
        parts = ["## Память", ""]
        if facts_lines:
            parts.extend(facts_lines)
            parts.append("")
        if cmd_lines:
            parts.append("Последние команды на этом устройстве:")
            parts.extend(cmd_lines)
        return "\n".join(parts) + "\n"

    # Попытка 1: preview 200
    cmd_lines = [_cmd_line(c, 200) for c in commands]
    block = _assemble(cmd_lines)
    if len(block) <= MAX_MEMORY_BLOCK:
        return block

    # Попытка 2: preview 100
    cmd_lines = [_cmd_line(c, 100) for c in commands]
    block = _assemble(cmd_lines)
    if len(block) <= MAX_MEMORY_BLOCK:
        return block

    # Попытка 3: обрезаем команды с конца (оставляем самые свежие)
    while cmd_lines:
        cmd_lines.pop()
        block = _assemble(cmd_lines)
        if len(block) <= MAX_MEMORY_BLOCK:
            return block

    # Только факты
    return _assemble([])


# ── Построение истории чата для LLM ──────────────────────────────────────

# Маркеры онбординговых ответов (фильтруем из истории, когда устройства уже подключены)
ONBOARDING_MARKERS = [
    "нет подключённых устройств",
    "нет подключенных устройств",
    "подключить устройство",
    "запустить IruAgent.exe",
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
    modes = modes or {}
    if modes.get("pipeline"):
        return await _process_pipeline_subagents(
            user_message=user_message,
            device_id=device_id,
            device_info=device_info,
            all_devices=all_devices,
            send_command_fn=send_command_fn,
            get_file_link_fn=get_file_link_fn,
            chat_history=chat_history,
            user_id=user_id,
            chat_id=chat_id,
            device_profile=device_profile,
            modes=modes,
            poll_task_id=poll_task_id,
        )

    cfg = load_llm_config()

    # Собрать промпт с информацией обо всех устройствах
    devices_block = build_devices_block(all_devices)
    os_info = device_info.get("os", "Windows")
    hostname = device_info.get("hostname", "unknown")
    os_version = device_info.get("os_version", "")

    profile_block = build_device_profile_block(device_profile)

    # machine_guid для памяти устройства
    machine_guid = (device_profile or {}).get("machine_guid") or None
    # Строковый user_id для user_memory (fallback на anon_machine_guid)
    mem_user_id = str(user_id) if user_id else (f"anon_{machine_guid}" if machine_guid else None)
    memory_block = build_memory_block(machine_guid, mem_user_id)

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
        device_memory_block=memory_block,
        os_rules=os_rules,
        current_datetime_msk=current_datetime_msk,
    )

    # Выбранные пользователем режимы (кнопка "+" в UI)
    pipeline_forced = bool(modes.get("pipeline"))
    autonomous = bool(modes.get("autonomous"))
    if pipeline_forced or autonomous:
        extra = []
        if pipeline_forced:
            extra.append(
                "КРИТИЧЕСКИ ВАЖНО: режим конвейера АКТИВЕН. Твоё ПЕРВОЕ действие — "
                "вызов create_plan с массивом steps. Без create_plan ты нарушишь контракт. "
                "НЕ выполняй execute_cmd/write_content/web_search до create_plan. "
                "После каждого шага вызывай mark_step.\n\n"
                "ЗАПРЕЩЕНО возвращать [[SUGGEST_PLAN: ...]] в pipeline-режиме. "
                "Этот маркер недопустим — ты УЖЕ в режиме плана. Вместо маркера "
                "ОБЯЗАТЕЛЬНО вызови tool create_plan с массивом steps.\n\n"
                "Если ты не знаешь как разбить задачу на шаги — всё равно сделай "
                "попытку create_plan с 2-3 разумными шагами. "
                "Маркер не является валидным ответом."
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

    _pipeline_sp_retries = 0          # счётчик попыток: LLM вернул SUGGEST_PLAN без create_plan в pipeline
    _PIPELINE_SP_MAX_RETRIES = 2      # максимум повторных «напоминаний»

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
                # Retry: 1 повторная попытка при 5xx / таймауте
                resp = None
                for _attempt in range(2):
                    try:
                        resp = await client.post(
                            f"{cfg['base_url']}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {cfg['api_key']}",
                                "Content-Type": "application/json",
                            },
                            json=request_json,
                        )
                        resp.raise_for_status()
                        break
                    except httpx.HTTPStatusError as _he:
                        if _he.response.status_code >= 500 and _attempt == 0:
                            print(f"[llm] 5xx retry: {_he.response.status_code}")
                            await asyncio.sleep(2)
                            continue
                        raise
                    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as _ne:
                        if _attempt == 0:
                            print(f"[llm] network retry: {type(_ne).__name__}")
                            await asyncio.sleep(2)
                            continue
                        raise
            except httpx.HTTPStatusError as e:
                print(f"[llm] HTTP error: {e.response.status_code} {e.response.text[:500]}")
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                print(f"[llm] network error: {type(e).__name__}: {e}")
                raise RuntimeError("Сервис ИИ временно недоступен. Попробуйте через минуту.")
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

            # ── ЗАЩИТА: если LLM вернул [[SUGGEST_PLAN:...]] вместе с tool_calls,
            # отменить tool_calls — маркер означает «не выполнять, показать плашку»
            _content_text = assistant_msg.get("content") or ""
            _sp_match = re.search(r'\[\[SUGGEST_PLAN:\s*[^\[\]]+?\s*\]\]', _content_text)
            if _sp_match and assistant_msg.get("tool_calls"):
                _dropped = len(assistant_msg["tool_calls"])
                _cmds_preview = ", ".join(
                    tc["function"]["name"] for tc in assistant_msg["tool_calls"][:5]
                )
                print(f"[llm] SUGGEST_PLAN guard: маркер найден в content, "
                      f"ОТМЕНЯЮ {_dropped} tool_calls [{_cmds_preview}] "
                      f"(user_id={user_id}, chat_id={chat_id})")
                # Убираем tool_calls из сообщения, оставляем только текст с маркером
                assistant_msg = {
                    "role": assistant_msg.get("role", "assistant"),
                    "content": _content_text,
                }

            # ── ЗАЩИТА pipeline: LLM вернул SUGGEST_PLAN без create_plan ──
            # В pipeline-режиме маркер недопустим. Даём LLM ещё шанс вызвать create_plan.
            if (pipeline_forced and _sp_match
                    and not assistant_msg.get("tool_calls")):
                _pipeline_sp_retries += 1
                print(f"[llm] pipeline: LLM вернул только SUGGEST_PLAN без create_plan, "
                      f"форсим продолжение (попытка {_pipeline_sp_retries}/{_PIPELINE_SP_MAX_RETRIES}, "
                      f"user_id={user_id}, chat_id={chat_id})")
                if _pipeline_sp_retries <= _PIPELINE_SP_MAX_RETRIES:
                    messages.append(assistant_msg)
                    messages.append({
                        "role": "system",
                        "content": (
                            "НАПОМИНАНИЕ: ты в pipeline-режиме. Вызови create_plan сейчас. "
                            "Маркер SUGGEST_PLAN запрещён."
                        ),
                    })
                    continue
                # Исчерпали попытки — пропускаем, ответ уйдёт как обычный текст

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
                    is_long_running = fn_args.pop("long_running", False)
                    try:
                        if is_long_running:
                            # GUI/long-running: запускаем и ждём 3 сек, потом возвращаем успех
                            fn_args["timeout"] = 5
                            try:
                                tool_result = await send_command_fn(
                                    target_device, "execute_cmd", fn_args,
                                )
                            except Exception as lr_e:
                                if "Таймаут" in str(lr_e):
                                    tool_result = {
                                        "stdout": "Приложение запущено (long_running)",
                                        "stderr": "",
                                        "returncode": 0,
                                        "error": None,
                                    }
                                else:
                                    raise
                        else:
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

                    # Автозапись команды в память устройства
                    if machine_guid and "error" not in tool_result:
                        try:
                            db.add_command_memory(
                                machine_guid=machine_guid,
                                device_id=target_device,
                                command=fn_args.get("command", ""),
                                intent=None,
                                exit_code=int(tool_result.get("returncode", -1)),
                                stdout=tool_result.get("stdout"),
                                stderr=tool_result.get("stderr"),
                                user_id=mem_user_id,
                            )
                        except Exception:
                            print("[llm] Failed to write command memory")

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
                            _push_tasks_view(poll_task_id, created_task_ids)
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
                            _push_tasks_view(poll_task_id, created_task_ids)
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
                                tavily_data = None
                                async with httpx.AsyncClient(timeout=20.0) as tavily_client:
                                    for _tavily_attempt in range(2):
                                        try:
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
                                            break
                                        except (httpx.HTTPStatusError, httpx.ConnectError,
                                                httpx.ReadTimeout, httpx.ConnectTimeout) as _te:
                                            is_5xx = isinstance(_te, httpx.HTTPStatusError) and _te.response.status_code >= 500
                                            is_net = isinstance(_te, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout))
                                            if (is_5xx or is_net) and _tavily_attempt == 0:
                                                print(f"[llm] tavily retry: {type(_te).__name__}")
                                                await asyncio.sleep(2)
                                                continue
                                            raise
                                if tavily_data is None:
                                    tool_result = {"error": "Поиск временно недоступен. Попробуйте позже."}
                                else:
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
                                tool_result = {"error": f"Поиск временно недоступен: {e}"}

                    commands_log.append({
                        "command": f"[web_search] {fn_args.get('query', '')[:80]}",
                        "device_id": target_device,
                        "result": tool_result if not isinstance(tool_result, dict) or "error" in tool_result else {"ok": True},
                        "iteration": iteration + 1,
                    })

                elif fn_name == "remember_fact":
                    if not mem_user_id:
                        tool_result = {"result": "Не удалось сохранить факт: пользователь не идентифицирован"}
                    else:
                        try:
                            fact_id = db.add_user_fact(
                                user_id=mem_user_id,
                                text=fn_args.get("text", ""),
                                category=fn_args.get("category"),
                            )
                            tool_result = {"result": f"Запомнил факт о тебе (id={fact_id})"}
                        except Exception as e:
                            print(f"[llm] remember_fact EXCEPTION: {e}")
                            tool_result = {"error": str(e)}

                elif fn_name == "forget_fact":
                    if not mem_user_id:
                        tool_result = {"result": "Факт не найден"}
                    else:
                        try:
                            ok = db.delete_user_fact(mem_user_id, int(fn_args.get("fact_id", 0)))
                            tool_result = {"result": "Факт удалён" if ok else "Факт не найден"}
                        except Exception as e:
                            print(f"[llm] forget_fact EXCEPTION: {e}")
                            tool_result = {"error": str(e)}

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
- Файл IruAgent.exe

Шаг 1: Скачать IruAgent.exe.

Шаг 2: Запустить IruAgent.exe двойным кликом.
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

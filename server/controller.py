"""
controller.py — LLM-планировщик ИРУ v3.4

Принимает текстовую задачу пользователя, через DeepSeek переводит в
последовательность команд PowerShell/cmd, отправляет агенту на выполнение,
анализирует результаты и формирует финальный ответ.

Два инструмента для LLM:
  - execute_cmd: выполнить команду на устройстве
  - get_file_link: получить ссылку для скачивания файла с устройства

Поддержка:
  - Мультиустройства (LLM знает все подключённые устройства пользователя)
  - Память чатов (последние 50 сообщений подаются в контекст)

Макс 8 итераций (tool-call loop).
"""

import json
import os
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

## Подключённые устройства
{devices_block}

## Текущее устройство (по умолчанию)
ID: {current_device_id}
Hostname: {current_hostname}
ОС: {current_os} ({current_os_version})

## Доступные инструменты

### 1. execute_cmd
Выполнить команду на устройстве.
- command (string, обязательно): команда для выполнения
- timeout (integer, по умолчанию 30): таймаут в секундах
- shell (string, по умолчанию "auto"): "powershell", "cmd" или "bash"
- device_id (string, опционально): ID устройства. Если не указан — \
выполняется на текущем устройстве.

### 2. get_file_link
Получить временную ссылку для скачивания файла с устройства.
- file_path (string, обязательно): полный путь к файлу на устройстве
- device_id (string, опционально): ID устройства

## Правила
1. Пользователь описывает задачу на естественном языке.
2. Определи, на каком устройстве нужно выполнить задачу. Если пользователь \
указывает конкретное устройство (по имени, hostname или ID) — используй \
параметр device_id. Если не указывает — выполни на текущем устройстве.
3. Для Windows — используй PowerShell. Для Linux — bash.
4. Анализируй результат каждой команды перед следующим шагом.
5. Если команда завершилась ошибкой — попробуй другой подход (макс. 8 итераций).
6. По завершении — дай короткий понятный ответ на русском языке.
7. НИКОГДА не выполняй опасные команды (форматирование дисков, удаление \
системных файлов, отключение антивируса) без явного подтверждения.
8. Если задача не связана с компьютером — просто ответь текстом.
9. Если пользователь просит скачать/передать файл — используй get_file_link.
10. Кодировка: ВСЕГДА добавляй в начало КАЖДОЙ команды PowerShell: \
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; \
Это обязательно для корректного отображения русского текста. \
При чтении/записи файлов явно указывай кодировку: \
Get-Content -Path file -Encoding UTF8; Set-Content -Path file -Encoding UTF8 -Value $text.
11. У тебя есть память — ты помнишь предыдущие сообщения в этом чате. \
Используй контекст разговора для более точных ответов.
12. НИКОГДА не используй Markdown-разметку в ответах: никаких **, *, #, ```, - и т.д. \
Отвечай чистым текстом без форматирования.
13. Для работы с приложениями используй программные интерфейсы (COM, WMI), а не эмуляцию клавиш. \
Примеры:
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
"""


# ── Определения инструментов ─────────────────────────────────────────────

TOOLS = [
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
    }
]

MAX_ITERATIONS = 8


# ── Промпт для режима без устройств (помощник по настройке) ─────────────────

ONBOARDING_PROMPT = """\
Ты — ИРУ (Интеллектуальный Режим Управления), ИИ-ассистент для управления \
компьютером через естественный язык.

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

    system_msg = SYSTEM_PROMPT_TEMPLATE.format(
        devices_block=devices_block,
        current_device_id=device_id,
        current_hostname=hostname,
        current_os=os_info,
        current_os_version=os_version,
    )

    # Формируем messages: system + история чата (без текущего сообщения) + текущее
    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        # История уже содержит текущее сообщение user (оно было сохранено до вызова)
        # Берём все сообщения кроме последнего, фильтруя онбординговые ответы
        history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    commands_log = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for iteration in range(MAX_ITERATIONS):
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg["model"],
                    "messages": messages,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "max_tokens": cfg.get("max_tokens", 1024),
                    "temperature": cfg.get("temperature", 0.0),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            assistant_msg = choice["message"]
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
                    "training_context": training_context,
                }

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])

                # Определить целевое устройство
                target_device = fn_args.pop("device_id", None) or device_id

                if fn_name == "execute_cmd":
                    try:
                        tool_result = await send_command_fn(
                            target_device, "execute_cmd", fn_args,
                        )
                    except Exception as e:
                        tool_result = {"error": str(e)}

                    commands_log.append({
                        "command": fn_args.get("command", ""),
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

                else:
                    tool_result = {"error": f"Неизвестная функция: {fn_name}"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
                })

    # Формируем контекст для записи тренировочных данных (предел итераций)
    training_context = {
        "os": device_info.get("os", ""),
        "hostname": device_info.get("hostname", ""),
        "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
    }
    return {
        "answer": "Достигнут лимит итераций. Последние результаты в логе.",
        "commands": commands_log,
        "training_context": training_context,
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

    system_msg = ONBOARDING_PROMPT.format(instruction_text=INSTRUCTION_TEXT)

    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        history_msgs = build_chat_messages(chat_history[:-1])
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": cfg.get("max_tokens", 1024),
                "temperature": cfg.get("temperature", 0.0),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    answer = data["choices"][0]["message"].get("content", "")
    return {"answer": answer, "commands": []}

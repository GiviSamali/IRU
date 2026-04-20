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
7. Если получишь ошибку BLOCKED — сообщи пользователю, что эта команда недоступна в бета-тестировании. \
Если получишь CONFIRM_REQUIRED — ОСТАНОВИСЬ, не повторяй команду и не пытайся её переформулировать.
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
14. ЗАПРЕЩЕНО использовать here-string синтаксис (@'...'@ или @"..."@) в командах. \
Here-string требует переноса строки после открывающего маркера, а команды передаются \
одной строкой — это всегда вызывает ошибку. Вместо этого:
  - Для многострочного текста: используй Set-Content с экранированными строками, \
например: Set-Content -Path file.txt -Value ("строка1`nстрока2`nстрока3") -Encoding UTF8
  - Для длинных строк: используй конкатенацию через +, или переменные.
  - Для JSON: формируй строку напрямую, например: $json = '{{"key": "value"}}'; \
Set-Content -Path file.json -Value $json -Encoding UTF8
15. Для путей к рабочему столу и папкам пользователя — ВСЕГДА используй путь из \
профиля устройства (раздел "Профиль устройства" выше), а не $env:USERPROFILE\\Desktop. \
На многих машинах рабочий стол перенесён в OneDrive и $env:USERPROFILE\\Desktop не существует. \
Если в профиле указан "Рабочий стол: C:\\Users\\user\\OneDrive\\Desktop" — используй именно этот путь.
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

MAX_ITERATIONS = 5


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


def build_device_profile_block(profile: dict | None) -> str:
    """Сформировать блок профиля устройства для промпта.
    Содержит информацию о железе, путях, пользователе."""
    if not profile:
        return ""

    lines = ["\n## Профиль устройства"]

    if profile.get("username"):
        lines.append(f"Пользователь Windows: {profile['username']}")
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

    system_msg = SYSTEM_PROMPT_TEMPLATE.format(
        devices_block=devices_block,
        current_device_id=device_id,
        current_hostname=hostname,
        current_os=os_info,
        current_os_version=os_version,
        device_profile_block=profile_block,
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

    _timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=_timeout) as client:
        for iteration in range(MAX_ITERATIONS):
            print(f"[llm] iteration {iteration+1}/{MAX_ITERATIONS}, messages={len(messages)}")
            try:
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
                        "max_tokens": cfg.get("max_tokens", 4096),
                        "temperature": cfg.get("temperature", 0.0),
                    },
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
                "max_tokens": cfg.get("max_tokens", 4096),
                "temperature": cfg.get("temperature", 0.0),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    answer = data["choices"][0]["message"].get("content", "")
    return {"answer": answer, "commands": []}

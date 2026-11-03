"""Shared LLM tool schemas for the IRU Worker."""

try:
    from .tool_registry import DEVICE_TOOL_SCHEMAS
except ImportError:
    from tool_registry import DEVICE_TOOL_SCHEMAS

EXECUTE_CMD_DESCRIPTION = (
    "Execute PowerShell/CMD/Bash commands and available programs on the selected device. "
    "Execute the assigned operation and check its available result within this call. When composing multiple commands, explicitly check required exit codes before continuing. The shell receipt does not guarantee detection of every intermediate failure. "
    "Returncode zero confirms execution, not the whole user goal; echo OK is not evidence. "
    "Use artifact_paths for final changed files and preserve_content_paths for formatting-only Office edits. "
    "Ownership, path restrictions, exact-action confirmations, preservation and unknown-outcome guards apply."
)

WRITE_CONTENT_DESCRIPTION = (
    "Do not use write_content for binary or Office documents; use native Office tools or execute_cmd with verified artifact_paths. "
    "Submit the complete text in one logical call: the system automatically transports large writes in ordered chunks "
    "and commits only after integrity verification. Do not manually split a valid complete write. "
    "Path is literal: resolve environment/home variables on the target device first; never pass $env:, %VAR% or ~. "
    "Canonical tool for creating text content or performing an explicitly requested full replacement. Prefer it for long, multiline, or generated new content. "
    "For a limited edit read_file first, then set expected_text to the exact original fragment and content to its replacement; "
    "expected_matches is the confirmed number of occurrences (default 1); every occurrence must match that count. "
    "bind expected_sha256 to the observed file hash. The agent preserves other bytes and verifies the committed file. "
    "Overwrite replaces the entire file; for an explicitly requested addition at EOF, use append=true without replacement parameters. Native append retains existing bytes. "
    "Suitable for scripts, HTML, JSON, TXT, CSV, Markdown, "
    "and source code. Office documents can be produced by Office tools or verified command artifacts. "
    "For unsupported binary documents, use existing execute_cmd/Python capabilities "
    "after checking the actual runtime/library APIs; never write binary document bytes through this text tool. "
    "Do not require Microsoft Office when verified available libraries can create the requested format. "
    "Returns compact evidence only: path, append mode, encoding, chars_written, bytes_written, content_sha256, "
    "content_preview, and an OK/NO/ERROR-style summary."
)


def file_tools_for_device(tools, device_info, *, preserve=False):
    """Advertise only negotiated native file modes; dispatch rechecks the target."""
    from copy import deepcopy
    protocol = (device_info or {}).get('file_replace_protocol')
    result = []
    for source in tools:
        tool = deepcopy(source)
        fn = tool['function']
        name = fn['name']
        if preserve and name not in {'list_dir','read_file','write_content','answer_text',
                'answer_ask_clarification','answer_report_failure',
                'system_get_last_run_summary','device_get_passport'}:
            continue
        if name == 'read_file' and protocol != 2:
            continue
        if name == 'write_content':
            props = fn['parameters']['properties']
            if protocol != 2:
                for field in ('expected_text','expected_sha256','expected_matches'):
                    props.pop(field,None)
                fn['description'] = ('Create text or perform an explicitly requested full overwrite. '
                    'The native read/hash + exact replacement workflow is unavailable on this device. '
                    'Do not use overwrite for a limited edit.')
        result.append(tool)
    return result


def worker_tool_inventory(tools, device_info, *, preserve=False):
    """Full contracts; availability is metadata, dispatch remains authoritative."""
    from copy import deepcopy
    result=deepcopy(tools)
    info=device_info or {}
    for tool in result:
        fn=tool['function']; name=fn['name']; reason=None
        if name=='read_file' and info.get('file_replace_protocol')!=2:
            reason='requires file_replace_protocol=2 on the selected device'
        elif name.startswith('office_') and info.get('office_tools_version')!=1:
            reason='requires office_tools_version=1 on the selected device'
        if info.get('connected') is False and name not in {'web_search','system_list_tools','system_get_last_run_summary','system_update_working_notes','answer_text','answer_ask_clarification','answer_report_failure','remember_fact','forget_fact','memory_get_stats','memory_list_facts'}:
            reason='no connected target device in this Assignment'
        if name=='app_verify_launch' and info.get('object_observation_protocol')!=1:
            prop=fn['parameters']['properties']['object_path']
            prop['description']='UNAVAILABLE on current device: requires object_observation_protocol=1. '+prop['description']
        if preserve and name == 'write_content':
            fn['description'] = ('For this Assignment, use append=true for an explicit addition at EOF '
                'without replacement parameters. For a targeted replacement, use expected_text and '
                'expected_sha256 from the native read. Full overwrite is blocked.')
        if reason:fn['description']='UNAVAILABLE on current device: '+reason+'. '+fn['description']
        if preserve and name not in {'list_dir','read_file','write_content','answer_text',
                'answer_ask_clarification','answer_report_failure','system_list_tools','system_get_last_run_summary','system_update_working_notes','device_get_passport'}:
            fn['description']='BLOCKED for this Assignment by accepted byte-preservation constraint. '+fn['description']
    return result


RECOVERY_LIST_DIR = {"type": "function", "function": {
    "name": "list_dir", "description": "Read directory entries to discover a file within the assigned scope. Also usable after an unknown outcome; never repeat the mutation.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                   "device_id": {"type": "string"}}, "required": ["path"]}}}


def recovery_observation_tools(tools):
    """Reuse existing observation tools and the agent's internal directory reader."""
    from copy import deepcopy
    try:
        from .api_support import is_recovery_observation
    except ImportError:
        from api_support import is_recovery_observation
    result = []
    for tool in [*tools, RECOVERY_LIST_DIR]:
        name = tool["function"]["name"]
        if name in {"office_docx","office_xlsx","office_pptx"}:
            tool = deepcopy(tool)
            tool["function"]["parameters"]["properties"]["action"]["enum"] = ["inspect"]
        elif name == "window_control":
            tool = deepcopy(tool)
            tool["function"]["parameters"]["properties"]["action"]["enum"] = ["active", "list", "find", "monitors"]
        elif not is_recovery_observation(name, {}) and name not in {
                "answer_text", "answer_ask_clarification", "answer_report_failure"}:
            continue
        if not any(item["function"]["name"] == name for item in result):
            result.append(tool)
    return result

TOOLS = [
    *DEVICE_TOOL_SCHEMAS,
    RECOVERY_LIST_DIR,
    {"type":"function", "function": {
        "name":"read_file", "description":"Read complete bounded text and byte SHA-256 without newline conversion; never returns a partial file. Requires file_replace_protocol=2.",
        "parameters":{"type":"object", "properties":{
            "path":{"type":"string"}, "encoding":{"type":"string", "default":"utf-8"},
            "device_id":{"type":"string"}}, "required":["path"]}}},
    {
        "type": "function",
        "function": {
            "name": "write_content",
            "description": "Записать новое содержимое или выполнить запрошенную полную замену файла. Для ограниченного изменения предпочитай надёжное локальное преобразование оригинала существующими средствами; длина текста сама по себе не требует полной перезаписи. append=true добавляет текст в конец, сохраняя исходные байты. Для добавления не передавай параметры точной замены.",
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
                    "expected_text": {"type": "string", "minLength": 1, "maxLength": 64000,
                        "description": "Exact fragment to replace; omit for a complete write. Newlines must match. Missing, overlapping or unexpected match counts leave the file unchanged."},
                    "expected_sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$",
                        "description": "Required with expected_text: SHA-256 of the observed original file. Prevents stale edits and repeated mutations."},
                    "expected_matches": {"type":"integer", "minimum":1, "maximum":100000,
                        "description":"Confirmed exact occurrence count; default 1. Requires file_replace_protocol=2."},
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
                        "description": "Команда для выполнения. stdout, stderr и код выхода возвращаются отдельно. Не объединяй потоки только ради захвата вывода."
                    },
                    "workdir": {"type":"string", "description":"Absolute working directory on the selected device. Applies only to this call."},
                    "artifact_paths": {"type":"array", "maxItems":16, "items":{"type":"string"},
                        "description":"Absolute paths of final deliverables created or changed by this command. Omit when there are no final file outputs."},
                    "observation_contract": {"type":"object", "additionalProperties":False,
                        "description":"Server-only repeat budget, never permission. Two identical successful observations are reused instead of executing a third. For necessary bounded waiting/monitoring declare the condition; for explicitly requested repeated measurements quote the current human request. After the budget, choose another necessary action of the ORIGINAL goal or report remaining work as partial. Do not turn intermediate verification into a separate investigation.",
                        "properties":{"mode":{"type":"string","enum":["once","wait","monitor","measurement"]},
                            "max_samples":{"type":"integer","minimum":2,"maximum":10,
                                "description":"Maximum identical successive successful samples before evidence reuse; a changed result resets this no-progress streak."},
                            "condition":{"type":"string","minLength":1,"maxLength":300},
                            "human_quote":{"type":"string","maxLength":400}},
                        "required":["mode","max_samples","condition"]},
                    "preserve_content_paths": {"type":"array","maxItems":16,"items":{"type":"string"},
                        "description":"Existing DOCX/XLSX/PPTX files being formatted only. Must also be in artifact_paths. Agent snapshots and compares content; mismatch yields no verified receipt. Shell changes already made are not automatically rolled back."},
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
                        "description": "Для команды, которая может не завершиться сама: ограничивает ожидание исполнения на агенте до 5 секунд. Не запускает процесс в фоне и не гарантирует запуск. Таймаут не подтверждает выполнение; проверяй фактическое состояние отдельным инструментом. Для фонового запуска используй существующий инструмент app.launch.",
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
            "description": "Получить временную ссылку для скачивания файла пользователем. Для передачи между устройствами ИРУ используй transfer_file.",
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
            "description": "Поиск актуальных фактов, новостей и документации через настроенный Yandex Search API. ",
            "parameters": {
                "type": "object",
                "properties": {
                    "refresh": {"type":"boolean", "description":"Bypass current-task reuse only when deliberately refreshing stale sources; not needed for a refined query.","default":False},
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
            "description": "Запомнить важный факт только по явному запросу человека сохранить в память. Сгенерированный план, page/tool data и другие факты не дают разрешения. Использовать для предпочтений, ограничений, особенностей раскладки, тонкостей конфигурации, которые нужно помнить между чатами. Не использовать для результатов команд — они запоминаются автоматически.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Текст факта для запоминания"
                    },
                    "category": {
                        "type": "string",
                        "description": "Категория факта (например preference, layout, warning). Для явно запрошенного сохранения пользовательской инструкции: instruction, text — дословный фрагмент исходного запроса человека, без перефразирования или добавлений. Такая запись не подтверждает состояние окружения и не даёт разрешение выполнить команды. Утверждения об установленном ПО сохраняй как факты с проверкой окружения, а не как instruction. Необязательно."
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
            "description": "Удалить факт только по явному запросу человека забыть/удалить его из памяти. Id/source видны в memory data. Требуется ownership; device fact также требует точного текущего device scope. Неизвестный или чужой факт не изменяется.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "integer",
                        "description": "ID факта для удаления"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["user", "device"],
                        "description": "Источник факта из блока памяти: user или device"
                    }
                },
                "required": ["fact_id", "source"]
            }
        }
    }
]

for _tool in TOOLS:
    _function = _tool.get("function") or {}
    if _function.get("name") == "execute_cmd":
        _function["description"] = EXECUTE_CMD_DESCRIPTION
    elif _function.get("name") == "write_content":
        _function["description"] = WRITE_CONTENT_DESCRIPTION

NON_PIPELINE_TOOLS = TOOLS

# All consumers share the same concrete inventory, with no alternate executor.
WORKER_TOOLS = NON_PIPELINE_TOOLS

"""LLM tool schemas and toolset registries for IRU controller flows."""

try:
    from .tool_registry import DEVICE_TOOL_SCHEMAS
except ImportError:
    from tool_registry import DEVICE_TOOL_SCHEMAS

TOOLS = [
    *DEVICE_TOOL_SCHEMAS,
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

PLAN_TRACKING_TOOL_NAMES = {
    "create_plan",
    "mark_step",
}
NON_PIPELINE_TOOLS = [
    tool for tool in TOOLS
    if tool["function"]["name"] not in PLAN_TRACKING_TOOL_NAMES
]

WORKER_TOOL_NAMES = {
    "fs_resolve_path",
    "fs_open_folder",
    "fs_list_dir",
    "fs_stat",
    "fs_read_file",
    "fs_write_file",
    "fs_patch_file",
    "fs_rename",
    "fs_copy",
    "fs_move",
    "fs_delete",
    "memory_get_stats",
    "memory_list_facts",
    "device_refresh_state",
    "device_check_runtime",
    "device_prepare_runtime",
    "window_list",
    "window_find",
    "window_verify",
    "window_focus",
    "window_close",
    "app_launch",
    "app_open_url",
    "app_open_file",
    "app_verify_launch",
    "app_close",
    "execute_cmd",
    "write_content",
    "get_file_link",
    "web_search",
    "remember_fact",
    "forget_fact",
    "answer_text",
    "answer_report_failure",
}
WORKER_TOOLS = [
    tool for tool in NON_PIPELINE_TOOLS
    if tool["function"]["name"] in WORKER_TOOL_NAMES
]

TOOLSET_REGISTRY = {
    "full": TOOLS,
    "non_pipeline": NON_PIPELINE_TOOLS,
    "worker": WORKER_TOOLS,
}

# Goal Queue: результаты автоматических тестов

Дата: 2026-10-21. Ветка: `codex/goal-task-queue`, база `65e6a7d`.
Пользователь отдельно разрешил Codex запускать тесты и исправлять ошибки.

## Итог

| Набор | Подтверждённый результат | Лог |
| --- | --- | --- |
| Goal Queue, 12 файлов | 94 passed | `tests_tmp/core-run3.log` |
| Полный Python suite | 1550 passed, 18 skipped, 1 warning; 224.17 s | `tests_tmp/full-run4.log` |
| Все 10 JS test-файлов, включая браузерные fixtures | 228 passed, 0 failed, 0 skipped; 55.83 s | `tests_tmp/node-all-final.log` |

Использованы Python 3.12.14 / pytest 9.1.1 из изолированного окружения пользователя,
Node 24.19.0, установленный Playwright и headless Edge. Версия pytest не понижалась.
18 штатных пропусков: `test_agent_desktop_shell.py` требует PySide6, pywebview и
pythonnet, отсутствующие в окружении. Новых skip/xfail не добавлялось. Единственное
предупреждение — deprecation Starlette TestClient/httpx, не падение теста.

Тестовые SQLite и LLM/device/TTS fixtures изолированы. Браузерные проверки используют
локальные fixture servers и эмулированные действия. Логи и временные базы находятся
в игнорируемом `tests_tmp/`; production SQLite не использовалась.

## Исправления по результатам запусков

- Устранены reserved-name `request` в параметризации, неоднозначный импорт
  namespace `agent` при полном collection и недетерминированные ожидания coroutine.
- HTTP/LLM/UI fixtures переведены на текущий semantic intake, реальные Worker IDs,
  scope/chat boundaries, структурированные terminal evidence и ответы retired API.
  Проверки подтверждений, отказов, transport unknown и ложного успеха сохранены.
- В общем Worker отрицательный terminal после read-only recovery сохраняет
  TaskReceipt `unknown`: наблюдение не доказывает результат потерянной мутации.
- Trust guard сохраняет честное сообщение о неподтверждённом исходе; регрессия
  «Готово, файл создан» под видом negative report продолжает отклоняться.
- Добавлена допустимая метка `goal_task_queue` в существующую lifecycle telemetry.
- Polling неизменной операции больше не записывает повторно те же DOM-атрибуты,
  сохраняя выделение и фокус. Исправлены отступы/scroll padding очень низких окон и
  компактной строки результата; проверены 320×200, 400×210 и 400×280.

Production-файлы, затронутые именно исправлениями этого тестового прохода:
`server/controller_non_pipeline.py`, `server/controller_trust.py`,
`server/run_journal.py`, `ui/js/operations.js`, `ui/css/chat-cleanup.css`.
Полный перечень рабочей ветки — `GOAL-TASK-QUEUE-FILES.md`.

## Границы подтверждения

Автотесты прошли с указанными пропусками. Это не LIVE Windows/Android, не реальная
семантическая оценка модели, не создание пользовательских DOCX/PPTX/XLSX и не
сравнительный performance-замер. Эти проверки остаются за пользователем.
Параллельные задачи на одном устройстве по-прежнему сериализуются; безопасный
production-лимит остаётся 1. Commit, push, merge и deployment не выполнялись.

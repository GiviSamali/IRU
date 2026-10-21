# Goal Queue: проверка и обновление

Рабочая ветка: `codex/goal-task-queue`, исходная main: `65e6a7d`.
Рабочая копия: `C:\Users\russa\OneDrive\Desktop\IRU\goal-task-queue`.

Пользователь разрешил Codex запускать автоматические тесты и исправлять ошибки.
Текущие результаты и ограничения записываются в GOAL-TASK-QUEUE-TEST-RESULTS.md.
LIVE, реальные Office-файлы и сравнительные замеры остаются отдельной приёмкой
пользователя. Автотесты с эмуляцией устройств не подтверждают LIVE.
Публикация, deployment и изменение production-БД отдельно не разрешены.

## Команды тестов для пользователя

Запускать в отдельной среде разработки с зависимостями `requirements.txt` и
`requirements-dev.txt`, без production-токенов и подключения реальных агентов.
Ниже указан уже найденный Python; при использовании своей venv заменить только
`$queuePython`. Используется изолированное окружение пользователя; зависимости не понижались.

```powershell
Set-Location -LiteralPath 'C:\Users\russa\OneDrive\Desktop\IRU\goal-task-queue'
$queuePython = 'C:\Users\russa\AppData\Local\IRU\goal-queue-test-venv\Scripts\python.exe'
$env:IRU_DB_PATH = Join-Path $env:TEMP ('iru-test-' + [guid]::NewGuid().ToString('N') + '.sqlite')
$env:PYTHONIOENCODING = 'utf-8'
& $queuePython -m pytest tests/test_goal_result_storage.py tests/test_goal_decision_contract.py tests/test_goal_routing_context.py tests/test_goal_quota_atomicity.py tests/test_goal_queue_metrics.py tests/test_goal_parallel_scheduler.py tests/test_goal_restart_recovery.py tests/test_goal_unified_intake.py tests/test_goal_memory_authorization.py tests/test_goal_no_implicit_probe.py tests/test_goal_window_scope.py tests/test_goal_retired_plan_api.py -q
```

Следующий набор сохраняет старые имена некоторых файлов, но проверяет общий Worker.
Исторические PLAN-квитанции в P0-проверках — данные совместимости, не старый движок.

```powershell
& $queuePython -m pytest tests/test_tool_only_protocol.py tests/test_p0_failure_recovery.py tests/test_pipeline_recovery_receipt.py tests/test_plan_stability.py tests/test_plan_execution_freedom.py tests/test_plan_confirmation_continuation.py tests/test_controller_pipeline_budget.py tests/test_device_isolation.py tests/test_memory_authority_regressions.py tests/test_browser_integration.py tests/test_multi_device_completion.py tests/test_web_search.py tests/test_transfer_controller.py tests/test_action_confirmation.py tests/test_transport_outcomes.py tests/test_window_input_continuation.py tests/test_settings_and_offline_memory.py -q
& $queuePython -m pytest -q
```

Голосовая модель состояний и UI: первая команда запускает Node-тесты без браузера;
вторая требует подготовленной Node-среды с Playwright и Edge:

```powershell
$env:NODE_PATH = 'C:\Users\russa\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:IRU_TEST_PYTHON = $queuePython
node --test tests/voice-session.test.cjs tests/smart-ui.test.cjs
node --test tests/chat-recovery-browser.test.cjs tests/smart-ui-browser.test.cjs
```

Браузерный набор поднимает локальный fixture server и браузер с заглушками API/голоса; это запуск
тестов пользователем, а не LIVE устройства. Включает ожидание зависимости после
долгой паузы вкладки и завершение фоновой задачи после переключения чата.

Не интерпретировать AST parsing или `node --check` как прохождение этих команд.
Сохранять полный вывод ошибок: возможные проблемы миграции старых ожиданий должны
быть исправлены, а не скрыты пропуском тестов.

## LIVE и производительность

Пользователь проверяет A–J из исходной цели и таблицы
`GOAL-TASK-QUEUE-ACCEPTANCE.md`. Особенно нужны отрицание/обсуждение без действий,
продолжение по прежнему результату, отмена одной ожидающей задачи, отказ исходного
исследования, перезапуск без повторной мутации и реальные DOCX/PPTX/XLSX из одной
зафиксированной фактической основы. Проверять содержимое файлов и источники;
наличие пути или returncode=0 не доказывает весь результат. Microsoft Office не
должен быть обязательным: Worker использует существующие инструменты и доступные
Python-библиотеки. Android и Windows отмечать как разные фактические проверки.

Для сравнения выполнить одинаковые сценарии на baseline и кандидате с одинаковыми
моделью, параметрами, устройствами, входными материалами и лимитом одного Worker.
Сохранить отдельные согласованные копии SQLite и идентификаторы корневого
Orchestrator и всех связанных Workers. Экспортёр читает БД в режиме read-only,
не запускает задачи и не подтверждает корректность файлов. Заменить заполнители:

```powershell
& $queuePython tools/goal_queue_metrics.py --db '<копия-baseline.sqlite>' --owner 123 --task-id '<root-id>' --task-id '<worker-id>' --label baseline-65e6a7d --output '<новый-baseline.json>'
& $queuePython tools/goal_queue_metrics.py --db '<копия-candidate.sqlite>' --owner 123 --task-id '<root-id>' --task-id '<worker-id-1>' --task-id '<worker-id-2>' --label goal-queue --output '<новый-candidate.json>'
```

Добавить `--task-id` для каждого участника сценария. Неполный список не годится
для сравнения. Отсутствие token events — отсутствие измерения, не нулевые затраты.
Проверять `token_counts_complete`: пропуск хотя бы одного значения оставляет
соответствующую сумму `null`, а не ноль или частичную сумму. Сравнивать вызовы,
токены, время и подтверждённые результаты вместе; уменьшение
числа вызовов при потере результата не является улучшением. Старый P0-03 mock
harness в `docs/performance` предназначен для своего исторического checkout.

## Порядок обновления после отдельного разрешения

1. Завершить тесты и LIVE-приёмку. Сохранить baseline revision, ревизию кандидата,
   рабочую конфигурацию сервиса и местонахождение фактической БД из `IRU_DB_PATH`.
2. Остановить единственный процесс сервера на согласованном стенде. Сделать
   согласованную резервную копию SQLite вместе с необходимыми WAL-данными через
   SQLite backup или после корректного закрытия соединений. Не копировать только
   основной файл работающей WAL-БД. Проверить восстановимость копии на стенде.
3. В копии проверить повторную инициализацию новой схемы, сохранность истории,
   квитанций, файлов и связей владения. Миграция добавляет таблицы goals/tasks/
   dependencies/results/request-id/revisions/capacity; пользовательская история
   не очищается. Старый уникальный индекс одного активного Worker заменён индексом
   и триггерами вместимости. Сам факт запуска не доказывает успешность миграции.
4. Обновить только согласованные исходники/зависимости и UI. Настройка
   `IRU_WORKERS_PER_OWNER=1` обязательна для первого развёртывания. Это число
   Workers пользователя внутри одного процесса, не число процессов Uvicorn.
   Существующий process lock БД и process-local WebSocket registry требуют одного
   серверного процесса. Не заменять установленный unit шаблоном вслепую: сохранить
   существующие пути, окружение и секреты. `deploy/deploy.sh` сам меняет unit и
   перезапускает сервис, поэтому эта инструкция его автоматически не вызывает.
5. После разрешённого старта проверить ревизию, active/running сервиса, счётчик
   перезапусков, локальный/публичный HTTP, опубликованные UI-файлы и восстановленные
   задачи. Прерванные running/confirmation/input получают неизвестный исход и
   автоматически не повторяются. Доказанно не начатые goal-задачи продолжают очередь;
   неуспешный источник не разблокирует потребителей.
6. Старые PLAN API возвращают контролируемый unsupported. Обновить клиентский UI,
   очистить устаревший web cache при необходимости и проверить голосовые команды,
   подтверждение точной опасной команды и исторические отчёты.

Новый Office Engine и новый протокол агента не добавлялись; изменения agent/ и
native AgentShell в этой ветке отсутствуют. Обновление сервера и web UI не требует
обязательной пересборки EXE по этому изменению. Устройства должны иметь совместимый
существующий агент и реально доступные библиотеки выбранного способа создания
документов. Любая сборка/загрузка агента — отдельное действие; Windows build script
по умолчанию загружает архив, поэтому без такого разрешения не запускать его.

Параллелизм 2–4 пока допускается только в изолированных проверках. Конфликтующие
задачи на одном устройстве сериализуются целиком, включая ожидание подтверждения;
это защищает изменяемые файлы и UI, но не доказывает параллельную работу нескольких
Office-задач на одном ПК. Серверные задачи и разные устройства имеют отдельные
сценарии изоляции. Повышение production-лимита требует отдельной приёмки.

## Откат

До обновления сохранить код и согласованную БД как одну пару. После допуска новых
задач откат только к старому коду может потерять понимание новой очереди: остановить
приём, зафиксировать результаты после обновления и согласовать восстановление пары.
Не удалять таблицы, пользовательские файлы или новые результаты для имитации отката.
Не воспроизводить операции с неизвестным исходом. Инструкция не разрешает сам откат.

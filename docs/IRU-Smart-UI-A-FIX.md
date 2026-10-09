# Smart UI A-FIX — достоверный статус и компактный Task

Основа: codex/agentshell-webview, 9bce14fbe55be0e4c02fe59a486ed6232196cad4.
Изменения в рабочей копии; commit/push этого этапа не выполнялись.

## Исправление

Раньше сообщения SQLite содержали content/commands, но не итоговый task_receipt,
статус, задачи/шаги PLAN или длительность. После открытия истории Smart UI
повторно выводил статус из неполных данных. Кроме того, отсутствие результата
инструмента превращалось в waiting, а done мог выглядеть успешным без evidence.

Добавлена совместимая миграция messages.task_metadata. Это серверный снимок
уже полученного результата: итоговый статус, выбранные поля receipt, tasks,
ID задачи, режим, заголовок и elapsed milliseconds. Он записывается с текстом и
командами в одной SQLite-транзакции и возвращается прежним chat history API.
Снимок не содержит confirm_data, WebSocket, токены или весь runtime task object.
История сохраняет outcome после очистки runtime-задач/рестарта; прежние проверки
владельца чата остаются в силе. API задачи дополнен presentation_status,
task_mode и elapsed_ms, чтобы текущий итог совпадал с сохранённым. Статус реального
execution, confirmation futures и Worker loop не менялись. Фиксация истории
добавлена к обычному завершению, PLAN, отмене, результатам подтверждения и
onboarding; отдельно сохраняется существующий ответ при runtime exception.

Unknown — отдельный статус «Результат не подтверждён». Waiting используется
для известных pending/queued/confirm. Простой done и отсутствие error не
доказывают завершение цели. Success берётся из достаточного server receipt,
существующего структурированного grounded terminal с evidence/basis либо
завершённых структурированных задач/шагов. Отрицательный итог/goal_completed=false
не перекрывается положительным done. Терминальный отчёт до последующей операции
не считается подтверждением её результата. Произвольный текст не классифицируется.

Task остаётся одним из четырёх прежних блоков. Для обычной задачи с одним
успешным tool action, подтверждённым итогом и известной длительностью менее 30
секунд он отображается строкой «✓ Завершено» и кнопкой «Подробности». Журнал
остаётся доступен, раскрытие не выполняет операций и переживает rerender.

Полная Task-карточка сохранена для PLAN (включая одношаговый), нескольких
операций, работы от 30 секунд, неизвестной длительности, подтверждений,
ошибок, recovery с несколькими действиями и partial/blocked/failed/unknown.
Обычный завершённый разговор без операций не получает ложную Task-карточку;
явный отрицательный результат не скрывается. File/Action и download API прежние.

Старые сообщения мигрируются без придуманных receipts. Если достаточное
подтверждение раньше не сохранялось, восстановить его ретроспективно нельзя:
UI показывает неподтверждённый результат, а не выдуманный успех или ожидание.

## Файлы

- server/database.py: миграция, whitelist и чтение/запись снимка.
- server/task_runtime.py: только точки сохранения сообщений и их metadata.
- server/routers/tasks.py: presentation metadata и сохранение confirmation/deny.
- ui/js/smart-ui.js: статусы, подтверждение результата, компактный вариант Task.
- ui/js/chat.js: metadata из polling и честные подписи unknown в деталях.
- ui/css/smart-ui.css: компактная строка и раскрытие журнала.
- tests/test_chat_task_outcomes.py: 16 новых Python regression cases.
- tests/test_onboarding_search.py: fake persistence принимает новый optional keyword.
- tests/smart-ui.test.cjs: доказательство статуса/компактности без чтения текста.
- tests/smart-ui-browser.test.cjs: 400/1280 px, история, раскрытие, отрицательные итоги.
- docs/IRU-Smart-UI-A-FIX.md: этот отчёт.

Worker/controller, Agent, WebView2, voice и Browser Bridge исходники не изменены.

## Проверки

- `node --test tests/*.test.cjs`: 157 passed.
- Связанные Python (история, confirmation, PLAN/revision/cancel, device isolation,
  onboarding): 135 passed.
- Полный Python-набор: 1038 passed / 18 skipped, один известный FastAPI/httpx warning.
  Для прежней коллизии agent.py/agent.shell файлы shell config/tray собирались
  первыми; весь набор из 72 test_*.py передавался pytest явно, без пропусков файлов.
- После последней узкой правки presentation при ошибке/onboarding и добавления
  двух дополнительных regression cases повторно запущены history/onboarding:
  26 passed. Полный набор после этих двух новых cases повторно не запускался.
- Пять изменённых Python-файлов проверены через compile(): OK.
- git diff --check: OK.

Команды в существующем окружении (Python suite использует bundled Python плюс
PYTHONPATH=$env:TEMP/iru-audit-20261002/deps; JS — bundled Node/Playwright):

```powershell
node --test tests/*.test.cjs
python -m pytest -q tests/test_chat_task_outcomes.py tests/test_confirmed_command_outcome.py tests/test_pipeline_plan_review.py tests/test_plan_confirmation_continuation.py tests/test_device_isolation.py tests/test_task_runtime_recovery_status.py tests/test_file_confirmation.py tests/test_onboarding_search.py

$afixTests = @('tests/test_agent_shell_config.py','tests/test_agent_shell_tray.py') + @(Get-ChildItem -LiteralPath tests -Filter 'test_*.py' | Where-Object { $_.Name -notin @('test_agent_shell_config.py','test_agent_shell_tray.py') } | ForEach-Object { 'tests/' + $_.Name })
python -m pytest -q @afixTests --basetemp "$env:TEMP/iru-afix-full-20261009"
python -m pytest -q tests/test_chat_task_outcomes.py tests/test_onboarding_search.py --basetemp "$env:TEMP/iru-afix-final-more-20261009"
git diff --check
```

18 skipped — native Qt/WebView2 тесты в bundled Python без этих зависимостей.
Исходники native host и Agent не изменялись; этот этап проверен в настоящем
headless Edge/Playwright на localhost с fake API/transport, без реальных
системных действий и платных LLM/STT. Сам skipped не является native smoke.

Синтетические снимки simple/partial и полный patch лежат в каталоге артефактов
Codex, отдельно от Git. Проверка на production не выполнялась: для неё сначала
нужно одобрить и опубликовать изменения, затем обновить сервер (миграция БД
выполняется при старте). Пересборка Agent для этого этапа не требуется.

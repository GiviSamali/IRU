# IRU OW-01 — Orchestrator + Single Worker v1

Дата проверки: 09.10.2026. Ветка: `codex/agentshell-webview`.
Изменения подготовлены для Code Review. Commit, push, merge и PR не выполнялись.

## 1. Исходный и итоговый HEAD

Оба HEAD: `f4995911399874639fe052c65b3bf55b50ce4371`.
Smart UI A-FIX и Windows Widget сохранены. Посторонний `codex_self_extension_tmp/` не изменялся.

## 2. Файлы

Новые модули: `server/orchestrator.py`, `server/worker_scheduler.py`, `server/worker_reports.py`.
Новые серверные тесты: `tests/test_orchestrator_worker.py`. Этот отчёт: `docs/IRU-OW-01.md`.

Изменены:

- `.gitignore` — исключение локального файла блокировки процесса.
- `server/routers/tasks.py` — диалоговый intake, делегирование, очередь, восстановление статуса, PLAN admission.
- `server/task_runtime.py` — существующий исполнитель получает ограниченный контекст и проверяет назначенные устройства; итог обновляет своё сообщение.
- `server/runtime_state.py` — queued и нормализованные terminal states; активный Worker не удаляется по обычному TTL.
- `server/main.py` — стартовое восстановление и завершение scheduler.
- `server/database.py` — обновление сообщения по ID, сохранение Worker Report и PLAN metadata.
- `server/controller.py`, `server/llm_usage.py` — существующая обёртка LLM измеряет latency, usage разделяется по entity.
- `server/controller_shared.py`, `server/memory_intent_guard.py` — релевантная память Worker и разрешения только исходного запроса человека.
- `server/controller_onboarding.py` — server-only Worker использует текущий terminal/basis protocol.
- `server/routers/voice.py` — поддержка новых terminal statuses.
- `ui/js/chat.js` — диалог отдельно от исполнения; привязка результата и отмены к task ID, восстановление и очередь.
- `ui/js/smart-ui.js` — существующий renderer учитывает Worker Report и queued.
- `ui/js/voice-session.js`, `ui/js/voice.js` — разговор во время Worker в текущем голосовом lifecycle.
- `tests/smart-ui-browser.test.cjs`, `tests/voice-session.test.cjs`, `tests/test_voice_plan.py` — регрессии UI, голоса, очереди и PLAN scope.

Исходники Agent, WebView2 host, Browser Bridge, Android и transport файлов не изменены.

## 3. Фактическая схема

```text
Пользователь / существующий голосовой контроллер
    -> POST /nl_command (orchestrate=true, request_id)
    -> Orchestrator: один structured decision основной LLM
         conversation / clarify -> ответ, без исполнительных инструментов
         task_status / cancel -> server-owned task_id
         delegate -> server validation -> SQLite admission
                     -> один Worker на owner_user_id
                     -> прежний SIMPLE / PLAN / broadcast / server-only controller
                     -> runtime guards -> прежний transport -> Agent
                     -> текущие evidence / terminal tools / receipt
                     -> нормализованный Worker Report v1
                     -> исходное сообщение + существующий UI и очередь озвучки
```

Окончательный результат автоматически доставляется сервером через прежний poll/TTS. Дополнительный LLM-вызов для переписывания каждого Worker Report не нужен. Следующая реплика Оркестратора получает отчёт как данные.

## 4. Ответственность

Оркестратор ведёт разговор и принимает одно решение через `orchestrator_decision`. У него нет `execute_cmd`, filesystem, browser DOM и других device tools. Отдельного intent classifier/router/judge нет; используется текущая основная модель.

Worker исполняет ограниченное поручение через прежние controller loops. Он не перепланирует бесконечно, не создаёт новых Worker и не получает автономных полномочий. Назначенный список устройств проверяется перед send, device tools, download link и transfer. Ownership и device-specific path policy остаются обязательными.

## 5. Контекст

Оркестратор: последние 12 реплик с лимитом 5000 символов, до 16 кратких device records, до 8 собственных задач, до 4 релевантных user facts по 300 символов. Общий контекст ограничен 14000 символами; сначала удаляются старые report payloads, затем история. Время и доступность имеют наблюдаемый timestamp; online не является доказательством готовности выполнения. Запросов к системному snapshot для conversation нет.

Worker: неизменённый исходный запрос человека и до двух предыдущих сообщений с небольшими структурированными результатами только назначенных устройств, релевантные факты и существующий локальный контекст controller. Полная история 50 сообщений и внутренняя история других Worker не передаются. Сгенерированные objective и parent summary остаются отдельной server metadata и не поступают в исполнительный prompt: они не могут стать новым пользовательским поручением. Runtime повторно выбирает original_request для SIMPLE, PLAN и server-only execution. Реальные snapshot/evidence исполнитель собирает прежними средствами по необходимости.

Поиск фактов использует общую лексическую релевантность, а не словарь намерений. Разрешения записи памяти вычисляются из исходного human request через ContextVar, а не из сгенерированной objective.

## 6. Один Worker и очередь

SQLite `worker_jobs`: owner, chat, task ID, state, payload, report, message ID, timestamps, request key. `BEGIN IMMEDIATE` и partial UNIQUE index не позволяют двум запросам занять один owner slot. До четырёх ожидающих поручений; FIFO. Другие пользователи имеют независимые slots.

Очередь принимается только после validation. Дневная квота списывается внутри той же SQLite transaction, после idempotency и queue checks, один раз за фактически принятое поручение. Ошибка admission откатывает и списание. Conversation, task status, PLAN proposal и повтор turn не расходуют квоту; IP/user anti-spam сохраняется. Admin exempt по серверному ID. ACK различает обработку и ожидание. Обычное и PLAN confirmation удерживают слот; независимый разговор остаётся доступен. Успех, ошибка и отмена освобождают слот. Queued cancellation не запускает исполнитель. Идемпотентность owner/request ID проверяет совпадение параметров; конфликт не является новым поручением.

Предел исполнения — один час, включая ожидание подтверждения. Таймаут даёт unknown; он не доказывает отсутствие уже начатого внешнего эффекта. Изменять текущую задачу на лету нельзя: Оркестратор предлагает явную отмену или новое поручение.

Очередь и отчёты переживают перезапуск. Активные задачи становятся unknown/interrupted; queued — cancelled/not executed. Автоматического повторного исполнения нет. Файловая блокировка рядом с DB запрещает второму серверному процессу восстанавливать задачи живого процесса. При одном текущем process-local Agent WS registry несколько uvicorn workers не поддерживаются.

## 7. Контракты

Decision v1: intent, answer, scope, objective, context_summary, target_device_ids, task_id, reference, reference_quote. Pydantic запрещает дополнительные поля и ограничивает размер. Server validates ownership, availability, exact assignment и неоднозначные ссылки. Повторное подтверждение PLAN остаётся за прежним source/revision guard. Явно неверное устройство не заменяется устройствами предложения PLAN.

WorkerReport v1:

```json
{"schema_version":1,"task_id":"...","worker_id":"worker-1",
 "status":"success","goal_completed":true,"summary":"...",
 "target_device_ids":["Second"],"artifacts":[],"evidence_refs":["step_1"],
 "requires_user_action":false,"error_code":null}
```

Состояния: queued, running, waiting_confirmation, success, partial, blocked, failed, cancelled, unknown. Report строится сервером из текущих receipt/journal. Для success нужны положительные tool evidence и подтверждённый terminal answer либо достаточный verified receipt. `returncode=0`, launch requested и отсутствие error сами по себе не доказывают достижение цели. Отрицательный итог authoritative. Artifact path принимается только из структурированного положительного результата назначенного устройства; текст модели не создаёт доверенную ссылку.

Task ID и message ID постоянны. Новые реплики, переключение чатов и F5 не переназначают старый ответ. Общая кнопка Stop предпочитает выполняющийся Worker очереди; карточка отменяет свой точный ID. Исторические сообщения сохраняют receipt, report и статус.

## 8. Голос

Использован один прежний voice-session, один SpeechRecognition и одна очередь TTS. Фоновые Worker IDs не занимают dialogue busy; краткий HTTP/LLM turn по-прежнему имеет request ticket. Wake word, sleep word, десятисекундное активное окно, команды и PLAN review используют прежний механизм.

Worker report ждёт завершения человеческой фразы и текущей озвучки. Ответы диалога имеют приоритет перед отчётами. Во время собственной озвучки микрофон временно приостановлен, чтобы не распознавать голос ИРУ. `усни` и остановка озвучки не отменяют Worker. Опасное подтверждение остаётся только кнопкой; обычное разрешение голосом привязано к текущей confirmation/revision, посторонний вопрос его не принимает.

При смене чата сохраняется прежнее отключение локальной voice session; исполнение и история продолжаются. Это не обещание реального full-duplex/barge-in.

## 9. Повторно использованные механизмы

Текущие SIMPLE/PLAN controllers, onboarding search/memory, task_runtime, Agent WS, transfer service, run_journal terminal/basis validation, trust/completion contracts, confirmations/revisions, cancel, ownership/path guards, SQLite messages, usage events, Smart UI Text/Task/File/Action и текущий voice/STT/TTS. Нового renderer, native audio engine, browser judge и новой модели нет.

## 10. Проверки

Фактически выполнено в проекте с bundled Python и существующими test dependencies:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH=(Join-Path $env:TEMP 'iru-audit-20261002/deps')
$py='C:/Users/russa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$tests=@('tests/test_agent_shell_config.py','tests/test_agent_shell_tray.py') +
  @(Get-ChildItem tests -Filter test_*.py |
    Where-Object { $_.Name -notin @('test_agent_shell_config.py','test_agent_shell_tray.py') } |
    ForEach-Object { 'tests/'+$_.Name })
& $py -m pytest -q @tests --tb=line --basetemp "$env:TEMP/iru-ow-p1-p2-full-20261009"
```

Это все Python test files; первые два указаны раньше остальных из-за существующей коллизии legacy `agent.py`/package imports в тестовом процессе. Не исключаются падающие tests.

Результат окончательного полного прогона: **1069 passed, 18 skipped, 1 warning за 134.48 s**. Предупреждение — существующая Starlette/httpx deprecation. Дополнительная проверка terminal TTL: 23 passed, 1 deselected (только уже пройденный 30-second test).

JS: `node --test tests/*.test.cjs`, NODE_PATH на bundled node_modules, IRU_TEST_PYTHON на тот же Python, PYTHONPATH на test dependencies: **163 passed, 0 failed**, включая настоящую страницу в headless Edge и MV3 extension regressions.

Native: `C:/Users/russa/PycharmProjects/PythonProject1/.venv/Scripts/python.exe -m pytest -q tests/test_agent_desktop_shell.py --tb=line --basetemp "$env:TEMP/iru-ow-native-final-20261009"`: **19 passed**. Эти тесты используют локальную страницу и synthetic media, не облачное распознавание. 18 skips полного Python прогона относятся к отсутствующим опциональным native/Qt компонентам bundled environment; отдельный native прогон использует имеющий их Windows venv.

Обязательный backend fake Worker задержан настоящим `asyncio.sleep(30)`, получены три ответа до его завершения, исполнитель вызван один раз, финальный отчёт один. Реальная HTML-страница принимает четыре фразы через FakeRecognition: поручение и три независимых вопроса при running Worker. Проверены поздний ответ после смены чата, восстановление без дубликата и отмена по ID. Это интеграционные mock-тесты, не production-демонстрация реальных устройств.

Покрыты FIFO/admission/overflow/idempotency, owner isolation, confirmation slot, terminal evidence, restart no replay, invalid decision, компактный контекст, memory permissions, server-only search terminal, старый cancel scope, отдельные usage entities, unknown/partial/blocked, queued UI, voice sleep/TTS/dangerous confirmation и существующие PLAN/broadcast/browser/auth/path regression suites.

## 11. Производительность и usage

Контролируемая задержка mock LLM — 10 ms. Измерены end-to-end run_turn:

| Сценарий | Время |
|---|---:|
| Короткий ответ при свободном Worker | 47 ms |
| Делегирование и сохранение admission | 63 ms |
| Короткий ответ во время Worker | 47 ms |
| Три ответа в обязательном 30-second test | 47 / 46 / 32 ms |

Нет ожидания завершения Worker и device/snapshot вызовов для pure conversation. Каждый диалоговый turn имеет один логический LLM request; provider HTTP retry остаётся прежним, поэтому это не обещание одного network attempt при ошибке API.

Mock provider usage проверен через существующую DB: Orchestrator 120 prompt / 15 completion, Worker 40 / 20. Это проверка раздельного учёта, не оценка реальной цены или экономии. `llm_usage_events.metadata.entity` различает сущности, phase показывает planner/worker этап, latency_ms измеряет успешный provider request. Модель и тарифный расчёт прежние. Делегирование добавляет один короткий Orchestrator request перед обычным Worker loop.

Для production после smoke собрать latency и фактические tokens по task/phase/entity, отдельно свободный и занятый Worker. SLA и реальные STT/TTS задержки сейчас не утверждаются.

## 12. Ручной Windows/Android smoke

После review и обновления открыть тот же сайт в desktop или браузере Android, включить голос и поручить создать небольшой новый файл на собственном ПК. Пока карточка running, задать три независимых вопроса. Проверить ответы без остановки Worker. Спросить статус; дать второе безопасное поручение и проверить queued. Дождаться результата A и исполнения B. Спросить, что сделано, и проверить ответы по history/report без повторного device action.

Отдельно проверить опасное действие: generic «да» не принимает его, доступна кнопка. Проверить обычное confirmation и PLAN revision, `усни`, пробуждение, остановку озвучки. На Windows скрыть/вернуть tray и F5: чат/задача не исполняются повторно. Переключить чат до позднего результата и убедиться, что он остаётся в исходном. Не использовать удаление файлов в smoke.

Этот реальный smoke **подготовлен, но не выполнен**: пользовательские Agent devices, Android и production STT/LLM не вызывались. В текущем окружении подтверждены server integration, настоящий browser renderer и native host tests.

## 13. Ограничения и риски

- Один процесс сервера на DB; это согласовано с текущим process-local WS registry. Масштабирование требует отдельной работы.
- Не resumable workflow: после рестарта нужен осознанный новый запрос; неизвестные внешние эффекты не повторяются.
- Старые HTTP-клиенты без `orchestrate=true` сохраняют прежний controller intake. Текущий сайт включает Orchestrator; их исполнение также проходит через один scheduler slot.
- Retrieval фактов ограничен простой лексической релевантностью; долгий разговор ограничен текущим compact tail, Context Engine не заменён.
- `worker_jobs` и `orchestrator_turns` пока не имеют отдельного retention cleanup; дальнейшее управление сроком хранения требует политики истории.
- Нет live production latency/tokens и проверки микрофона реального Android. Fake recognition не доказывает качество облачного STT.
- Системное действие, уже отправленное Agent, может завершиться после cancel/timeout; unknown не превращается в success.
- Сбой SQLite при сохранении report требует восстановления/операторской диагностики; нельзя объявлять новый успех или автоматически повторять действие.
- Таймауты/лимиты самого Worker остаются существующими; OW не ускоряет его выполнение и не расширяет автономность.

## 14. Diff и состояние review

Полный patch, включая новые файлы, подготовлен отдельно: `IRU-OW-01.patch` в артефактах этого задания. `git diff --check` прошёл; `py_compile` для всех 16 новых/изменённых Python-файлов и `node --check` для всех изменённых JS/CJS прошли. HEAD не меняется. Остановка после Code Review preparation; никаких Git publication действий.


## Дополнение Code Review: P1/P1/P2

1. Дневной лимит убран из предварительного `/nl_command`. `database.reserve_daily_worker_command` применяется из `WorkerScheduler.submit` внутри admission transaction. Проверены исчерпанная квота при conversation/status, повтор, переполнение очереди, неверное устройство, гонка за последним местом, смена дня и rollback после отказа INSERT.
2. `submit_worker.message` и финальный `user_message` контроллера — исходная human реплика. `proposed_objective`/`proposed_context_summary` хранятся отдельно; в Worker context их нет. Предложение PLAN сохраняет `cmd.message`, не model objective. Тест «посмотри папку» против «удали файлы» проходит для обычного и PLAN пути, включая устаревший caller, передающий objective в runtime. Это исправление границы handoff; новый семантический classifier или универсальное доказательство корректности всех решений Worker не добавлялись. Прежние effect/ownership/path/confirmation guards сохранены.
3. Context trimming работает независимо от tasks. Ограничены внешние device metadata; при превышении удаляются report payloads, история, факты, задачи и устройства, затем selected metadata. Поля ID не подменяются. Усечение отмечается context_truncated; отсутствие наблюдения не разрешает fallback.

Связанный прогон: `python -m pytest -q tests/test_orchestrator_worker.py -k 'not 30_second' tests/test_plan_limits.py tests/test_voice_plan.py --tb=short` — 43 passed, 1 deselected. Финальные прогоны: Python 1069 passed, 18 skipped, 1 existing deprecation warning; JS 163 passed, 0 failed. Compile и git diff --check прошли. Дополнительно создан focused diff `IRU-OW-01-P1-P2.patch` только этих исправлений. UI/native код в этих трёх исправлениях не менялся.

Ограничение: поскольку model objective/summary больше не передаются как исполнительный контекст, неясное поручение должно уточняться по исходной реплике и доступным структурированным данным. Generated routing text не служит источником новых действий. Live production smoke остаётся ручным.

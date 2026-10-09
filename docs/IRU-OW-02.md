# OW-02 — целевой аудит и минимальные исправления

09.10.2026. Ветка `codex/agentshell-webview`. Исходный HEAD: `4f7982740a7ad5a6cf1bcdb16f8c1dc4b689767a`. Commit/push/merge/PR не выполнялись.

## Подтверждённые проблемы

| Приоритет | Причина | Исправление |
|---|---|---|
| P0 | non_pipeline terminal_sufficient guard синтезировал завершение после промежуточной записи, даже когда Worker выбрал запись второго файла | Различные дальнейшие tool actions проходят прежние schema/effect/ownership/budget guards; точные повторные write/execute и прежние native verification loops не допускаются |
| P1 | handoff почти не содержал человеческие уточнения и мог захватить новые сообщения после ожидания Orchestrator LLM | Ограниченный снимок до вызова модели, data-only диалог и наблюдения, без изменения текущего поручения |
| P1 | Worker не получал надёжную ссылку на результат предыдущего задания | Source task IDs выбираются основной LLM; сервер проверяет owner/chat/device scope и обновляет данные источника перед стартом queued Worker |
| P1 | После решения основной модели отдельная complexity-модель повторно выбирала SIMPLE/PLAN | execution_mode в том же единственном Orchestrator decision. Явный simple пропускает классификацию; plan использует прежнее согласование; auto оставляет совместимость |
| P1 | При восстановлении предложения PLAN терялось многодевайсное назначение | Assignment device IDs и source IDs сохраняются в существующей message metadata и восстанавливаются; admission повторно проверяет ownership/availability |
| P2 | Только лексическая релевантность исключала общие предпочтения оформления | До двух owner-scoped preference facts дополняют релевантный набор, сохраняя лимиты и data-only семантику |
| P2 | Сведения о задачах не указывали чат, а latency failed LLM/RPC не была достаточной | Chat labels и предпочтение текущего чата в context; numeric timings успешных/ошибочных provider requests и Agent wait, включая подтверждённый вызов |

Новый router/classifier/judge не добавлен. Existing answer auditor сохранён: его отключение ради скорости ослабило бы trust contracts. Одна операция не стала доказательством всей задачи; Worker сохраняет исходный запрос, а generated objective/summary остаются в карантине.

## Baseline → after

Baseline зафиксирован до правок в отдельном временном SQLite, с контролируемыми model replies и fake transport. Байты в пользовательской файловой системе не создавались. Это архитектурное воспроизведение, не доказательство качества реальной модели.

| Метрика | Baseline | After |
|---|---:|---:|
| Создать два файла: выполненные tool writes | 1/2 | 2/2 |
| Worker LLM calls этого сценария | 2 | 3 |
| Cold mock wall time | 134.52 ms | 182.56 ms |
| Сохранено ранее согласованное имя | нет | да |
| Worker context в коротком сценарии | 329 chars | 696 chars |
| Orchestrator context этого сценария | 1103 chars | 1212 chars |
| Общие preference facts без совпадения слов запроса | 0 | 1 |
| Complexity calls после явного simple от Orchestrator | 1 | 0 |
| Выполняемые Worker на owner / queued slots | 1 / 4 | 1 / 4 |

Дополнительный call для второго файла — необходимое исполнение, не регрессия. Рост контекста оправдан сохранением необходимого имени/предпочтения. Cold mock timings зависят от Python/httpx и компьютера, не от production LLM; их нельзя трактовать как SLA или ускорение API. После явного simple сокращается один последовательный model request; реальное сокращение задержки и tokens требуется измерить на provider.

Локальная `llm_usage_events` имеет 0 записей. Production DB/API не подключались. Реальные prompt/completion tokens, модельное качество и production latency не выдумывались.

## Файлы и причины

- `server/controller_non_pipeline.py`: goal-aware continuation после verified intermediate action; deterministic duplicate write/execute detection. Прежние native window/app verification fast exits сохранены.
- `server/orchestrator.py`: semantic guidance для переходов conversation/clarify/delegate/status/cancel, mode/source references в существующем decision, chat labels, preferences, first response timing.
- `server/worker_context.py` (новый): ограниченная server-owned передача контекста. Исторические сообщения не становятся user instructions; старые step_id не являются текущими evidence. Source artifact path не переносится на неназначенное устройство.
- `server/routers/tasks.py`: frozen history до LLM, параметры handoff; подтверждённый RPC получает authenticated diagnostic scope только после ownership/nonce/device проверок.
- `server/worker_scheduler.py`: refresh явно указанных источников при старте из очереди, idempotency включает mode/source IDs, execution start timestamp.
- `server/controller_shared.py`: bounded general preferences без нового memory storage.
- `server/database.py`: дополнительные существующие JSON metadata для assignment/source IDs, numeric timing и безопасного diagnostic trace.
- `server/task_runtime.py`: пропуск повторной классификации только по явному simple; измерение send/wait и очистка pending при send failure. Device availability не кешируется.
- `server/run_journal.py`: existing metadata allowlist включает duration/event; ownership-checked confirmation binding.
- `server/controller.py`: latency failures/compatibility retry записывается тем же usage accounting.
- `tools/ow02_eval.py` (новый): воспроизводимые offline probes, estimate, ограниченный opt-in primary provider eval и local aggregate usage export.
- `tests/test_ow02_quality.py` (новый): 13 regression cases.

Smart UI, voice engine/session/reconnect, WebView2 host, Agent, WorkerReport v1 schema, pipeline planning/worker/recovery modules и tool implementations не переделывались.

## Контекст и безопасность

Worker получает ровно текущую human реплику как user message, до двух последних небольших журналов разрешённых устройств и data-only packet. Packet содержит до четырёх коротких исторических реплик и до трёх явно выбранных sources. Большие source summaries/контекст имеют отметку усечения. Дополнительные source artifacts ограничены device assignment; полные пути не режутся до выдуманного другого пути. Очередной Worker обновляет результаты источника в момент старта, но не подхватывает поздние независимые поручения как новые инструкции.

References не являются новым workflow engine/dependency graph: они передают данные. Failed/unknown source не превращается в success, внешнее состояние проверяется действующими инструментами. Source outside owner/current chat fail closed, без default-device fallback. Даже на valid source нельзя получить полномочия из его текста. Source и target для transfer по-прежнему должны быть назначены и принадлежать пользователю; реальные Desktop/path policies неизменны.

PLAN через execution_mode=plan всё ещё требует существующей consent/revision механики. Для auto/legacy requests классификация остаётся. Iteration/recovery budgets и single-call-per-iteration/terminal basis не увеличены. Текущие cancel, dangerous button confirmation, memory write intent и recent artifact guards сохранены.

## Проверки и измерения

Связанные проверки: 107 passed для ordinary/PLAN tool protocol, OW-02, confirmation outcomes и trace privacy; последующий targeted goal/protocol прогон — 60 passed. Первый полный прогон — 1111 passed, 18 skipped. Финальный полный прогон: 1111 passed, 18 skipped, 1 existing Starlette/httpx deprecation warning за 168.94 s. После последнего дополнения aggregate export: все 13 OW-02 tests прошли отдельно.

Полный JS: `node --test tests/*.test.cjs` — 184 passed, 0 failed; включены OW-01-DIALOG, mobile/desktop history, voice wake/idle/confirmation, Browser Bridge и widget.

В full Python включены все test_*.py, с двумя legacy agent import tests первыми для существующей namespace-коллизии. Bundled Python использует прежние test dependencies и уникальный --basetemp. Optional native Qt skips отделены от regression. Платные LLM/TTS не вызывались.

`diagnostic_trace` теперь содержит device_wait duration без аргументов, stdout, путей или токенов. Trace/start time сохраняются в message JSON metadata. `llm_usage_events` остаётся единственным учётом calls/tokens/cost. Failed LLM latency тоже учитывается. Timing для ordinary confirmation привязан после auth/ownership проверки. Для старых runs без новых trace полей agent_wait может быть неполным — это captured_agent_wait_ms, не выдуманная полная задержка.

Снять метрики завершённого production task локально на VPS (только агрегаты, без текста запросов):

```bash
python3 tools/ow02_eval.py --database /actual/path/to/iru.db --user-id OWNER_ID --usage-task-id TASK_ID
```

Путь указать фактический IRU_DB_PATH, не предполагать repository DB. Экспорт показывает entity calls/tokens/LLM latency, execution time, admission-to-start, captured RPC wait, tool results и Orchestrator first response. First response — серверное формирование ответа, не network/TTS playback time.

## Ограниченный real provider pilot

По умолчанию `python tools/ow02_eval.py` — offline, без сети/устройств. `--estimate --max-calls 10` печатает план и configured-price envelope без API calls.

Оценка на имеющихся repo/config rates deepseek-v4-flash: около $0.03159 для 10 решений основной модели, при консервативной input-byte upper estimate и output cap 1200. Это условная стоимость по настройкам, не гарантия текущего тарифа provider; reasoning/compatibility retries/billing требуют проверки actual usage.

```bash
python3 tools/ow02_eval.py --estimate --max-calls 10
# Только после решения пользователя запустить платные запросы:
python3 tools/ow02_eval.py --live --max-calls 3 --max-cost-usd 0.02 --output ow02-provider.json
```

Live mode делает только Orchestrator decisions на synthetic metadata. Не допускает Worker, не подключается к Agent и не выполняет системные действия. По умолчанию максимум 3 logical requests; максимум CLI — 10. Между запросами проверяется configured-price budget, actual provider tokens/cost сохраняются через существующий ledger во временной DB и выходном JSON. HTTP compatibility retries не равны дополнительной классификации, могут увеличить network attempts. Код credentials не печатает. Автоматически live не запускался.

10 reproducible classes: обсуждение Python; simple file; несколько документов/PLAN; известный и неизвестный file reference; status; независимый разговор при running task; новая таблица в очередь; two-device action; неполная формулировка. Routing/mode/device/source checks относятся к реальному provider только при --live, mock responses не доказывают язык/намерения модели.

## Device/voice smoke A–J

На двух пользовательских Windows agents: создать маленький новый TXT на givi; затем спросить о нём; передать по существующему transfer на Second; проверить итог и hash. Далее презентация через существующий PLAN с проверкой конечного PPTX. Пока Worker занят — три независимых вопроса, затем два новых поручения в очередь и status без повторного исполнения. Проверить independent actions на двух ПК/broadcast, offline target, missing file, unknown outcome и отказ от опасной команды. Повторить через имеющийся голос с sleep/wake.

Эти real device/voice сценарии подготовлены, но не выполнялись на пользовательских ПК. Не удалять существующие файлы в smoke; использовать новые уникальные имена.

## Оставшиеся ограничения

- Качество выбора intent/mode/source ID зависит от реальной основной LLM; требуется limited pilot, не только mocks.
- Дополнительное native verification после достаточного результата остаётся ограниченным прежними fast-exit правилами; сложные проверки лучше выразить текущим PLAN completion contract. Нужен live coverage редких смешанных native/info задач.
- Source references — данные, не универсальные dependencies/replanning. На failure источник помечен честно; Worker может уточнить/получить новую проверку, не получает разрешения из summary.
- Одна модель decision может выбрать неверный simple/plan. Не включены большой budget или автоматическое replanning для маскировки ошибки.
- Общие preference facts являются данными оформления, не новым source of authority. Не разработан новый memory engine или semantic retrieval.
- Model/context cost не оптимизировался удалением existing auditor/guards или сменой модели. Полные system/tool schemas остаются потенциальной дорогой частью; без реального качественного сравнения их агрессивно не урезали.
- При server restart сохраняется OW-01 policy: активное unknown, queued cancelled/not executed; при отказе самой DB продвижение может остановиться.

py_compile всех изменённых/новых Python и git diff --check прошли. Полный patch reverse-apply проверяется без изменения checkout.

Full patch для Code Review: IRU-OW-02.patch в артефактах задания. HEAD не менялся.

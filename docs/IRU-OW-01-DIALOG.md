# OW-01-DIALOG — естественные ответы и подробности исполнения

09.10.2026. Ветка `codex/agentshell-webview`, исходный HEAD `78f329922ed4f78c1689fa55c78f8d8f6f0fcbc0`.
Изменения подготовлены для Code Review. Commit/push/merge/PR не выполнялись.

## Причины избыточной речи

- `server/orchestrator.py:SYSTEM` передавал состояние устройств и очереди, но не разделял обязательные внутренние ограничения и уместную пользовательскую речь. ACK и task_status использовали формальные сообщения и технический WorkerReport.summary.
- `server/worker_scheduler.py:persist_report` и `/api/tasks/{task_id}` передавали исходный Worker answer прямо в пользовательский текст. Для отрицательного результата исходный answer мог заменяться report.summary, вместо сохранения отдельного полного отчёта.
- `ui/js/chat.js:pollTask` показывал task.answer. Smart UI не имел отдельного поля полного отчёта; обычные Task details на desktop по умолчанию были открыты.
- `server/voice.py:spoken_parts` мог запускать дополнительный voice_brief LLM request по длинному ответу, создавая самостоятельный пересказ для голоса.

## Изменения

Новый `server/response_presentation.py` формирует два представления одного серверного результата:

- `conversational_response`: человеческий итог по нормализованному WorkerReport v1 и действующим evidence contracts;
- `execution_details`: исходный Worker answer без обрезания, с сохранением протокола и журналов отдельно.

Worker, его final answer, controller loops и Tool/WorkerReport contracts не переписаны. Нет парсинга «Готово», словарной классификации пользовательских намерений, нового judge или LLM-пересказа каждого завершения.

Успех определяется прежним evidence-based report. Тип файла берётся из подтверждённого artifact path, расположение на Desktop — из совпадения с профилем именно этого пользователя и устройства. Число слайдов и факты из свободного текста не выдумываются. Для ошибки используются известные machine error codes; произвольный stderr не превращается в произносимую инструкцию или доказательство.

Неподтверждённый исход остаётся unknown; failed, partial, blocked и cancelled не становятся успехом. Неуспех не скрывается silent_on_success правилом отдельного успешного window/browser action. Само правило молчания при достаточном успехе сохраняется.

Исходный `task.answer` и GET API `answer` остаются прежними. Новые поля — отдельная проекция. История сохраняет короткий content и полный executionDetails в существующей JSON metadata, вместе с commands, tasks, receipt и Worker Report. При restore восстанавливаются оба представления. Сохранённый итог используется как снимок выполнения; поздний профиль устройства не переписывает старую реплику. При явном изменении на отрицательный/неопределённый outcome успешный снимок не перекрывает новый статус.

Оркестратор получает правила естественной подачи: без автоматической диагностики при приветствии, без постоянного приглашения дать команду, подробный технический ответ допустим по смыслу вопроса. Намерения, делегирование, очередь, исходное поручение как граница разрешений, quotas, ownership и cancel сохраняются. `show_execution_details` — только флаг представления существующего task_status: по просьбе показать весь отчёт возвращается полный отчёт, этапы, tools и receipt конкретной собственной задачи, без нового исполнения.

Smart UI сохраняет Text/Task/File/Action. Text использует conversationalResponse; полный ответ доступен в существующих разворачиваемых Task details на mobile и desktop. Проверенные Worker artifacts дополняют существующие File cards с прежним authenticated download API; URL и пути из прозы не становятся файлами или ссылками. Не создаётся отдельный экран/renderer. Existing confirmation/revision/actions сохранены; конкретная опасная команда остаётся видимой там, где нужна для решения пользователя.

TTS текущего OW Worker и Оркестратора использует пользовательскую реплику без отдельного редакторского LLM вызова. Lifecycle, очередь, wake/sleep/stop, idle reconnect и WebView2 не менялись. Информационный ответ на read-only задачу остаётся содержательным и полным: нельзя заменить прочитанное сообщение или ответ поиска словом «Готово».

## Примеры

| Ситуация | Раньше | Теперь |
|---|---|---|
| Приветствие | Диагностика зарегистрированного устройства и оговорки про verification | Естественный ответ основной LLM, например «Привет! Я на связи» |
| Принято поручение | «Поручение принято. Обработка началась» | «Хорошо, займусь» |
| Поручение ожидает | «Добавлено в очередь…» | «Записала поручение. Начну, когда закончу текущее» |
| Проверенная презентация на Desktop | Полная структура слайдов, пути и скрипты | «Презентация готова. Файл на рабочем столе» |
| Частичный результат с проверенной презентацией | Длинный отчёт о сбое | «Презентация готова. Но задача выполнена не полностью» плюс подтверждённая причина, если есть код |
| Нет права прочитать файл | stderr/служебный вывод | «Не получилось завершить задачу. Нет доступа к файлу или папке» |
| Нет достаточных доказательств | Технический unknown report | «Не могу подтвердить результат. Подробности сохранены в ходе выполнения» |

Примеры разговора иллюстрируют prompt policy, а не гарантируют точный текст реального provider response. Технические вопросы не ограничены новым числом слов. «10 слайдов» не произносится на основании одного лишь текста модели.

## Файлы

Новые: `server/response_presentation.py`, `tests/test_response_presentation.py`, этот отчёт.
Изменены: `server/orchestrator.py`, `server/worker_scheduler.py`, `server/database.py`, `server/routers/tasks.py`, `server/routers/voice.py`, `server/voice.py`, `ui/js/chat.js`, `ui/js/smart-ui.js`, `ui/css/smart-ui.css`, `tests/test_orchestrator_worker.py`, `tests/smart-ui.test.cjs`, `tests/smart-ui-browser.test.cjs`.

Agent, Worker controllers, Browser Bridge, WebView2 host, tool implementation и voice-session/recognition lifecycle не менялись.

## Проверки

- Новые Python regression tests: 29. Три новые регрессии после review проверяют inline-факты и смешанную обработку. Проверены типы документов, отсутствие выдуманных slide counts/paths, отдельные Windows profiles, все negative/waiting states, ошибка по code, read-only результаты, persistence/restore/API, одинаковый TTS/text без voice_brief, три вида разговора и exact-owned details request.
- Связанный Python прогон: 92 passed, 1 deselected (обязательный 30-second test проходит в полном прогоне).
- Полный Python: 1098 passed, 18 skipped, 1 warning (существующая Starlette/httpx deprecation), 133.27 s. Последний запуск: `python -m pytest -q` со всеми Python test files в указанном ниже порядке, `--tb=line --basetemp "$env:TEMP/iru-dialog-inline-full-20261009"`.
- `node --test tests/*.test.cjs`: 184 passed, 0 failed. Включены настоящая страница в headless Edge, mobile/desktop details, inert HTML, история/смена чата, negative statuses и все предыдущие voice idle/PLAN/confirmation/browser/widget tests.
- py_compile всех новых/изменённых Python файлов, node --check изменённых JS/CJS и git diff --check — прошли. Усиленные DIALOG browser tests с настоящим page.reload дополнительно прошли: 4/4.

Полный Python запускается существующим bundled Python с PYTHONPATH на test dependencies и уникальным --basetemp. Все test_*.py включены; `test_agent_shell_config.py` и `test_agent_shell_tray.py` идут первыми для обхода существующей коллизии legacy agent.py/package imports. Native host source не менялся; пропуски optional Qt environment отделены от regression. Платные LLM/TTS и реальные системные действия в этих проверках не выполнялись.

## Ограничения и ручная проверка

Факты зависят от существующих структурированных evidence. Если нет подтверждённого artifact path, итог остаётся общим; тип файла, число слайдов и Desktop не извлекаются из рассказа модели. Новый output schema для count metadata или новые проверки файлов не вводились.

Read-only ответы и подробные теоретические объяснения могут быть длинными, когда это содержательный результат. Legacy voice_brief helper сохранён для старых задач вне OW; текущие Worker/Orchestrator пути его обходят.

Реальная формулировка приветствий зависит от основной модели; лексической цензуры или regex-подмены ответа не добавлялось. Production LLM/STT/TTS smoke на устройствах пользователя ещё нужен.

После review/deploy проверить «Привет», «Как дела?», «Какие устройства подключены?», создание презентации, разговор во время работы, частичный результат и «Покажи все подробности выполнения». Сравнить Text/TTS, раскрыть Task, проверить File card и F5/переключение чатов. Нет оснований для пересборки агента.

Полный diff для review: `IRU-OW-01-DIALOG.patch` в артефактах задания; включает новые файлы. HEAD не изменён.


## Review P1/P2: inline-факты и смешанное чтение

`server/voice.py:spoken_parts` для Worker вызывает `answer_parts(..., keep_inline=True)`. Backticks как оформление убираются, но содержимое сохраняется: `config.ini`, `port=8080` и другие значимые inline-фрагменты произносятся. Fenced code, URL/Markdown обработка сохраняют прежнюю семантику. Дополнительного редакторского LLM-вызова нет.

`response_presentation.py` сохраняет полный содержательный ответ не только для известного набора observation tools, но и для валидированного grounded_report с `self_check.claims_completed_action=false`, когда нормализованный outcome успешен. Это существующий terminal contract и проверенный текущий basis, не новый intent classifier и не regex разбор shell-команды. Смешанные execute_cmd/read_file/вспомогательный write_content не скрывают ответ на вопрос за общим итогом действия.

Проверены execute-only обработка и смешанное чтение с созданием вспомогательного скрипта. Text сохраняет исходный ответ, TTS сохраняет имя и настройку. Unknown outcome и некорректный basis не пропускают содержательный ответ как подтверждённый результат. У grounded action report с claims_completed_action=true по-прежнему короткий итог, без чтения структуры слайдов.

Связанный прогон после этих изменений: 69 passed. Усиленный mixed target: 3 passed, 26 deselected. Полный финальный прогон после review: 1098 passed, 18 skipped, 1 existing deprecation warning. Compile и diff check прошли. JS не менялся в P1/P2; предыдущий полный JS прогон — 184 passed.

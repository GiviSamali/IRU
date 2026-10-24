# IRU — актуальное строение и важные узлы

Commit: 712f730757ae440108fc8bcd3c3b572c57124cbc (origin/main). Обновлено 2026-10-23.

1359 узлов / 3733 связей; 21 компонентов / 49 механизмов.

- Снимок origin/main 712f730757ae440108fc8bcd3c3b572c57124cbc; незакоммиченные изменения не включены.
- Степень, SCC и точка сочленения — структурные сигналы, а не доказанный runtime SPOF.
- Import/static_call и агрегированные depends_on считаются отдельно; contains/implements не являются потоком выполнения.
- Косвенные callback/registry и JavaScript calls не восстановлены; непривязанные модули явно отмечены.
- Риски условные; нагрузочное тестирование, production и физический Android не проверялись.

## Изменения строения

- PLAN/controller_pipeline удалён. Актуальная модель — Goal с Task DAG, ревизиями и закреплёнными результатами.
- Добавлены Office DOCX/XLSX/PPTX, сохранность содержимого, атомарная запись, chunk receipts и source-bound final artifacts.
- Добавлены execution_scope/window input, memory authorization, UI modes и новые pending confirmation пути.
- Учтён 712f730: текущая реплика не дублируется в context; durable token compaction отменена ee65621.
- CSS-слои и явные frontend import/link/script включены в физическую структуру; JS call graph не выдуман.

Статический граф: 1597 рёбер, 186 точек сочленения, 2 SCC с циклом.

Агрегированный граф: 576 рёбер, 0 точек сочленения, 4 SCC с циклом.

## Хранилище и транзакции

Общий контекст подключения к SQLite: WAL, foreign_keys, commit при нормальном завершении и закрытие соединения.

Почему важен: Общая точка доступа к хранилищу истории, Goal/Task и закреплённых результатов; степень пересчитана для нового снимка.

Условный риск: При ошибке доступа, блокировке или нарушении транзакции возможны сбои сохранения истории и заданий. Это потенциальная зона отказа, а не подтверждённый единый отказ всего IRU.

Проверки в исходнике: Контекстное закрытие соединения, foreign_keys и commit при нормальном выходе. Резервирование и восстановление базы по этому графу не доказаны.

Что проверять: Проверять запись/чтение заданий, конкурирующие транзакции, откат при исключении и восстановление после перезапуска.

{"static": {"incoming": 98, "outgoing": 0, "neighbors": 98, "reverse_reachable": 147, "articulation": true, "scc_size": 1}, "semantic": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}}

- server/database.py:31 @ 712f730757ae

## Центр выполнения tools

Основной controller tool-loop без удалённого pipeline: проверяет вызовы, scope, журнал, завершение, восстановление и итог.

Почему важен: Центр исполнения с большим числом прямых исходящих зависимостей; включает новые write/Office и recovery ветви.

Условный риск: Неверное завершение цикла или потеря текущего basis может дать несогласованный результат. Наличие вызова само по себе не доказывает исполнение.

Проверки в исходнике: В исходнике используются validate_tool_call_batch, validate_answer_text_payload и audit_answer_payload; текст инструмента нельзя заменять оптимистичным итогом.

Что проверять: Проверять tool → journal → answer → audit → receipt, execution_scope, Office/write результаты, остановку, восстановление наблюдений и отсутствие старого pipeline.

{"static": {"incoming": 0, "outgoing": 69, "neighbors": 69, "reverse_reachable": 0, "articulation": true, "scc_size": 1}, "semantic": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}}

- server/controller_non_pipeline.py:230 @ 712f730757ae

## Маршрутизация поручения

Decision содержит assignments для Goal/Task, ревизии, scope и memory_authorizations. run_turn отделяет admission от решения LLM.

Почему важен: Контекст в 712f730 исключает ровно текущее сохранённое сообщение по ID; прежние одинаковые реплики не удаляются. Текущий ввод передаётся отдельно.

Условный риск: Повреждённый Decision, усечённый ответ или сбой LLM оставляют решение непринятым.

Проверки в исходнике: Один orchestrator_decision, проверка схемы и finish_reason; повтор инструкций устранён в итоговом commit. Экспериментальная durable compaction отменена ee65621.

Что проверять: Проверять отсутствие дублирования текущей реплики, owner/chat/goal_revision, повтор request_key и различие Decision/admission.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 11, "neighbors": 13, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/orchestrator.py:52 @ 712f730757ae
- server/orchestrator.py:372 @ 712f730757ae
- server/orchestrator.py:402 @ 712f730757ae
- server/orchestrator.py:273 @ 712f730757ae

## Граница передачи контекста

Bounded handoff использует clip_context_text: сохраняет начало объекта и конец предложения в лимите; источники и verified artifacts фильтруются по owner/chat/device.

Почему важен: Связывает текущую реплику, историю, pinned results и последующие задачи. Токен-бюджеты/durable compaction не входят в итоговую версию.

Условный риск: Чужой или устаревший результат в handoff может исказить решение и нарушить изоляцию владельцев.

Проверки в исходнике: capture_history ограничен восемью сообщениями; исторические данные не дают полномочий. Полные закреплённые результаты передаются отдельно от краткого presentation.

Что проверять: Проверять отсутствие чужих артефактов, явное усечение и совместимость snapshot/report при восстановлении.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 11, "outgoing": 10, "neighbors": 15, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/worker_context.py:29 @ 712f730757ae
- server/worker_context.py:45 @ 712f730757ae
- server/worker_context.py:91 @ 712f730757ae
- server/controller_shared.py:400 @ 712f730757ae

## Жизненный цикл Worker

Task runtime исполняет принятые задания на разрешённых устройствах; поддерживает продолжение pending window input и общий non-pipeline controller.

Почему важен: Соединяет admission, выполнение, identity, отмену и отчёт. Результат одной задачи не означает достижение всей Goal.

Условный риск: Сбой между фактическим действием и сохранением результата может оставить неопределённый итог.

Проверки в исходнике: Ветка выполнения связана с отменой, target device scope и receipts; успех действия нельзя выводить из одного статуса запуска.

Что проверять: Проверять завершение/partial/blocked/unknown, подтверждения, disconnect и итог для нескольких устройств.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 11, "outgoing": 12, "neighbors": 18, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/task_runtime.py:834 @ 712f730757ae
- server/task_runtime.py:1481 @ 712f730757ae
- server/controller_non_pipeline.py:230 @ 712f730757ae
- server/controller.py:537 @ 712f730757ae

## Admission и восстановление

WorkerScheduler принимает Goal DAG транзакционно, проверяет ревизии и зависимости, закрепляет результаты, восстанавливает задания и продолжает оконный ввод.

Почему важен: Центр Goal/Task lifecycle и долговременной согласованности; старый независимый pipeline больше не является исполнителем.

Условный риск: Гонка admission или рассогласование payload/report может дать дубликат, потерянное задание или старый статус.

Проверки в исходнике: validate_task_graph до admission writes; submit_goal — all-or-none; owner/chat/device проверки; отсутствие await/Worker/LLM внутри admission-транзакции.

Что проверять: Проверять revision conflict, повтор request_key, waiting_dependency/input/confirmation, замену результатов, cancel_group и persist → restore.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 5, "outgoing": 11, "neighbors": 11, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/worker_scheduler.py:416 @ 712f730757ae
- server/worker_scheduler.py:721 @ 712f730757ae
- server/worker_scheduler.py:753 @ 712f730757ae
- server/worker_scheduler.py:758 @ 712f730757ae

## Транспорт решения LLM

Конфигурация и _chat_completion_request — общий транспорт модели, которым пользуются маршрутизация, исполнение и аудит.

Почему важен: Внешняя зависимость встречается в нескольких критичных путях.

Условный риск: Timeout, ответ неверной формы или исчерпание лимитов могут остановить решение/проверку; это не доказательство сбоя инструментов.

Проверки в исходнике: Конкретный транспорт и обработка ответа видны в controller.py; потребители отдельно валидируют свои контракты.

Что проверять: Проверять timeout, malformed tool call, усечение ответа, usage и разделение ошибок транспорта/протокола.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 5, "neighbors": 9, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/controller.py:83 @ 712f730757ae
- server/controller.py:177 @ 712f730757ae
- server/controller.py:143 @ 712f730757ae
- server/controller.py:127 @ 712f730757ae

## Контракты инструментов

canonical_tool_name нормализует имя; list_tools выбирает разрешённые tools. Registry/inventory связывает объявление инструмента с его использованием.

Почему важен: Registry и inventory включают native Office tools, write transport и актуальные контракты результатов. Статические показатели пересчитаны.

Условный риск: Изменение имени или схемы может нарушить совместимость LLM, журнала и исполнения.

Проверки в исходнике: Центральные определения и фильтрация инструментов уменьшают расхождения; достаточность runtime-проверок нужно проверять в каждом dispatcher.

Что проверять: Проверять Office schemas/validate_office_args, канонические имена, tool args, artifact receipts и ветви восстановления наблюдений.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 6, "outgoing": 3, "neighbors": 8, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/tool_registry.py:1070 @ 712f730757ae
- server/tool_registry.py:1100 @ 712f730757ae
- server/tool_inventory.py:1 @ 712f730757ae
- server/tool_registry.py:1130 @ 712f730757ae

## Структурный соединитель tools

Определения/инвентарь связывают канонические имена инструментов с поддерживаемым набором.

Почему важен: Центральная граница схем и исполнения tools. Топологический статус вычислен заново: прежнее утверждение о точке сочленения не переносится.

Условный риск: Расхождение схемы и executor может нарушить вызов или интерпретацию evidence; runtime SPOF по графу не доказан.

Проверки в исходнике: Статические evidence указывают registry и inventory. Динамический dispatch и резервирование графом не подтверждены.

Что проверять: Проверять совместимость catalog → schema → executor → journal, особенно при переименовании tools.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 10, "outgoing": 5, "neighbors": 14, "reverse_reachable": 28, "articulation": false, "scc_size": 2}}

- server/tool_registry.py:1070 @ 712f730757ae
- server/tool_registry.py:1100 @ 712f730757ae
- server/tool_inventory.py:1 @ 712f730757ae

## Доставка команды и identity

send_command_to_agent проверяет наличие устройства и владельца; область путей проверяется для execute_cmd; identity receipt сопоставляет зарегистрированные и наблюдаемые признаки.

Почему важен: Переход от серверного решения к действию на устройстве — граница фактического исполнения.

Условный риск: Disconnect, неправильный device scope или identity mismatch могут сделать результат недостоверным для нужного устройства.

Проверки в исходнике: Owner check; в identity receipt отсутствие сравнимых признаков оставляет unknown, несовпадение даёт mismatch.

Что проверять: Проверять target_device_id, nonce/ожидающий ответ, timeout, подтверждение и identity после выполнения.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 5, "outgoing": 14, "neighbors": 17, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/task_runtime.py:265 @ 712f730757ae
- server/task_runtime.py:191 @ 712f730757ae
- agent/core/runtime.py:31 @ 712f730757ae
- agent/core/runtime.py:125 @ 712f730757ae

## Граница локальных действий

Агентские execute_cmd/write_content и подготовка runtime выполняют действия на устройстве.

Почему важен: Локальное исполнение связывает shell/file/window действия и новые атомарные записи. Прежний статус точки сочленения не переносится.

Условный риск: Успешный запуск процесса не гарантирует достижение пользовательской цели или появление артефакта.

Проверки в исходнике: write_content и Office commit различают staged/committed/verified; после потенциального commit неопределённость не превращается в успех.

Что проверять: Проверять запись/чтение файла, фактический итог команды, платформенные пути и отличать launch_requested от завершения.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 2, "outgoing": 5, "neighbors": 7, "reverse_reachable": 29, "articulation": false, "scc_size": 1}}

- agent/core/actions.py:89 @ 712f730757ae
- agent/core/actions.py:192 @ 712f730757ae
- agent/core/actions.py:824 @ 712f730757ae
- agent/core/actions.py:1474 @ 712f730757ae

## Отмена и оперативное состояние

Оперативные состояния включают ожидание подтверждения, зависимости и оконного ввода; отмена принадлежит владельцу и согласуется с сохранённым Task/Goal.

Почему важен: Состояние используется несколькими ветвями выполнения.

Условный риск: Поздняя отмена и гонка с терминальным результатом могут оставить противоречивые проекции.

Проверки в исходнике: Отмена привязана к task и проверке владельца; запрос отмены и терминальная отмена различаются.

Что проверять: Проверять waiting_input/confirmation/dependency, cancelling/cancelled и восстановление latest payload/report.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 14, "outgoing": 0, "neighbors": 14, "reverse_reachable": 18, "articulation": false, "scc_size": 1}}

- server/runtime_state.py:150 @ 712f730757ae
- server/runtime_state.py:157 @ 712f730757ae
- server/database.py:1 @ 712f730757ae
- agent/core/local_state.py:1 @ 712f730757ae

## Полномочия и подтверждения

verify_access_token проверяет подпись и срок JWT; command_confirmation создаёт отдельный confirmation_id и определяет допустимый канал подтверждения.

Почему важен: Небольшая степень связности не снижает важность границы безопасности.

Условный риск: Ошибка в owner/device scope или привязке подтверждения может разрешить действие не тому владельцу/поручению.

Проверки в исходнике: Неверный/истёкший JWT возвращает None; опасные/deletion подтверждения не получают voice_allowed.

Что проверять: Проверять истёкший токен, чужое устройство/задание, повтор nonce и подтверждение именно ожидающей команды.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 10, "outgoing": 18, "neighbors": 18, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/auth.py:72 @ 712f730757ae
- server/command_confirmation.py:31 @ 712f730757ae
- server/command_confirmation.py:52 @ 712f730757ae
- server/controller_shared.py:291 @ 712f730757ae

## Граница доказательств результата

Журнал хранит шаги запуска; validate_answer_text_payload требует структуру ответа и проверяет basis текущего журнала.

Почему важен: Соединяет наблюдения инструмента с утверждениями итогового ответа.

Условный риск: Старый basis, отрицательный результат или текст без evidence могут привести к ложному успеху при обходе проверки.

Проверки в исходнике: При claims_completed_action отрицательные execute_cmd/write_content evidence отклоняются; валидатор возвращает исходный payload.

Что проверять: Проверять актуальность step_id, отсутствие подмены answer.text и разделение наблюдения/доказательства действия.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 8, "outgoing": 1, "neighbors": 9, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/run_journal.py:292 @ 712f730757ae
- server/run_journal.py:341 @ 712f730757ae
- server/run_journal.py:369 @ 712f730757ae
- server/run_journal.py:410 @ 712f730757ae

## Независимая проверка ответа

audit_answer_payload сопоставляет запрос, текущий журнал и ответ; при включённом аудите требует корректный JSON verdict.

Почему важен: Дополнительная граница перед принятием отчёта.

Условный риск: Аудитор зависит от LLM и конфигурации: отключённый аудитор не равен независимой успешной проверке.

Проверки в исходнике: Ошибка транспорта возвращает отрицательный verdict; невалидный JSON ограниченно повторяется. При выключенном аудите возвращается auditor disabled.

Что проверять: Проверять enabled/disabled, transport error, invalid JSON, отрицательный verdict и basis после repair.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 2, "neighbors": 9, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/answer_auditor.py:74 @ 712f730757ae
- server/answer_auditor.py:63 @ 712f730757ae
- server/controller_trust.py:212 @ 712f730757ae

## WorkerReport и честный итог

WorkerReport строится из текущего terminal answer, receipts и evidence; включает финальные проверенные артефакты Office/write с привязкой к задаче.

Почему важен: Итог становится источником состояния для интерфейса и хранения.

Условный риск: Если восстановление возьмёт старый report или текст модели вместо текущих доказательств, пользователь увидит ложный успех.

Проверки в исходнике: Success связан с grounded terminal и проверенными evidence. Финальные artifact receipts имеют SHA256/source_task_id; промежуточный файл не подменяет окончательный результат.

Что проверять: Проверять final artifact receipts, content preservation, Task report → pinned result → Goal view → UI → restore; pending и unknown не считать успехом.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 13, "neighbors": 14, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/worker_reports.py:6 @ 712f730757ae
- server/worker_reports.py:60 @ 712f730757ae
- server/worker_reports.py:41 @ 712f730757ae
- server/worker_scheduler.py:335 @ 712f730757ae

## Проекция результата в интерфейс

Проецирует Task и Goal, проверенные файлы и заменённые результаты; UI открывает файлы на исходном устройстве и показывает живое pending confirmation.

Почему важен: Связывает проверенный серверный итог с desktop/mini/mobile; последние коммиты исправляют приоритет ожидающих подтверждений и компактность mini.

Условный риск: Независимая интерпретация статуса или изменение терминального текста могут противоречить отчёту.

Проверки в исходнике: Источником результата должен оставаться report, а не декоративная подсветка или LLM обещание.

Что проверять: Проверять live/restored pending confirmation, source device для открытия файла, superseded tasks и совпадение goal status/report.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 6, "neighbors": 12, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/response_presentation.py:148 @ 712f730757ae
- server/response_presentation.py:188 @ 712f730757ae
- server/response_presentation.py:48 @ 712f730757ae
- server/response_presentation.py:76 @ 712f730757ae

## Goal / Task DAG

Общая цель состоит из проверяемых зависимых результатов и ревизий. goal_view оценивает все актуальные задачи, учитывая superseded/replacement.

Почему важен: Новая структура вместо старого PLAN. Один success не означает завершение всей цели.

Условный риск: Несогласованная ревизия или принятие старого результата может исказить итог Goal.

Проверки в исходнике: validate_task_graph ограничивает размер и запрещает циклы/отсутствующие зависимости; goal_view проверяет owner/chat и pinned result.

Что проверять: Проверять atomic submit_goal, goal_revision, retries/replacements, cancel_group и waiting_dependency.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 4, "outgoing": 11, "neighbors": 11, "reverse_reachable": 14, "articulation": false, "scc_size": 15}}

- server/worker_scheduler.py:232 @ 712f730757ae
- server/worker_scheduler.py:988 @ 712f730757ae
- server/goal_presentation.py:15 @ 712f730757ae
- ui/js/mini-operations.js:1 @ 712f730757ae

## Закреплённые результаты

_freeze_result сохраняет полные server-owned evidence; pinned_results проверяет ссылки на результаты зависимостей.

Почему важен: Опорная граница передачи результатов между заданиями Goal, отдельная от усечённого presentation summary.

Условный риск: Чужая или незавершённая зависимость может загрязнить handoff при обходе проверок.

Проверки в исходнике: Закрепление связано с success/goal_completed и той же транзакцией, что терминальная проекция; сами данные источника остаются untrusted.

Что проверять: Проверять result_id/SHA256, owner/chat/device, восстановление и отклонение неподтверждённых dependencies.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 7, "outgoing": 17, "neighbors": 18, "reverse_reachable": 22, "articulation": false, "scc_size": 22}}

- server/worker_scheduler.py:113 @ 712f730757ae
- server/worker_scheduler.py:141 @ 712f730757ae
- server/worker_scheduler.py:324 @ 712f730757ae

## Нативные Office документы

DOCX/XLSX/PPTX обрабатываются локальными библиотеками через схемы Office tools, без GUI автоматизации.

Почему важен: Новая цепочка schema → executor → атомарный commit → reopened file → final receipt.

Условный риск: Успешный save без проверки документа не доказывает корректный итог; ошибка после commit требует unknown.

Проверки в исходнике: Ожидаемый SHA256, safe_edit_package, временный файл, fsync, повторное открытие, проверка сохранности, os.replace/exclusive link.

Что проверять: Проверять edit conflict, content preservation, отсутствие библиотек, final artifact_role и привязку source_task_id.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 3, "outgoing": 0, "neighbors": 3, "reverse_reachable": 16, "articulation": false, "scc_size": 1}}

- server/office_tool_schemas.py:9 @ 712f730757ae
- server/office_tool_schemas.py:26 @ 712f730757ae
- server/office_tool_schemas.py:33 @ 712f730757ae
- agent/core/office_tools.py:507 @ 712f730757ae

## Сохранность содержимого Office

content_snapshot сравнивает содержимое при preserve_content; verify_preservation сверяет вновь открытый документ.

Почему важен: Граница между форматированием и непреднамеренным изменением данных.

Условный риск: Форматирование может повредить данные, если опираться только на статус сохранения.

Проверки в исходнике: Снимок содержимого до/после, проверка package и повторное открытие native библиотекой.

Что проверять: Проверять таблицы, формулы/структуру и явный отказ content_preservation_failed.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 4, "outgoing": 0, "neighbors": 4, "reverse_reachable": 33, "articulation": false, "scc_size": 1}}

- agent/core/office_content.py:1 @ 712f730757ae
- agent/core/office_tools.py:490 @ 712f730757ae

## Запись и финальные артефакты

write_in_chunks объединяет staged части в логическую запись; artifact verification и transfer связывают файл с устройством и исходной задачей.

Почему важен: Новая граница между доставкой байтов и доказанным финальным файлом.

Условный риск: Несовпадающий chunk receipt или изменённый файл после commit не должен давать success.

Проверки в исходнике: operation_id/chunk_index/hash, итоговые committed/content_sha256/chars_written/verified; _verify_artifact и transfer authorization.

Что проверять: Проверять разрыв передачи, digest, гонки файла, nonce/transfer purpose и final receipts.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 5, "outgoing": 3, "neighbors": 8, "reverse_reachable": 15, "articulation": false, "scc_size": 1}}

- server/write_transport.py:6 @ 712f730757ae
- agent/core/actions.py:200 @ 712f730757ae
- agent/core/actions.py:192 @ 712f730757ae
- agent/core/actions.py:44 @ 712f730757ae

## Оконный scope и ввод

validated_execution_scope проверяет область; window_input_required/choice формируют выбор пользователя; answer_window_input продолжает ожидающее задание.

Почему важен: Разделяет управление окнами и общее выполнение, не выдавая оконному действию дополнительные полномочия.

Условный риск: Устаревший выбор окна или ответ не тому pending input может направить действие неверно.

Проверки в исходнике: Проверяется scope/effects, owner и идентификатор ожидающего ввода.

Что проверять: Проверять неоднозначное окно, waiting_input, stale question ID и отсутствие расширения scope.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 12, "outgoing": 30, "neighbors": 35, "reverse_reachable": 22, "articulation": false, "scc_size": 22}}

- server/worker_scheduler.py:30 @ 712f730757ae
- server/worker_scheduler.py:959 @ 712f730757ae
- server/window_policy.py:65 @ 712f730757ae
- server/window_policy.py:96 @ 712f730757ae

## Разрешение записи памяти

Decision содержит MemoryAuthorization; memory-intent guard отделяет текущую явную авторизацию от истории.

Почему важен: Критичная граница между прочитанными данными и долговременной записью.

Условный риск: История/источник не должны самостоятельно разрешать remember_fact.

Проверки в исходнике: Контракт текущего turn и guard перед выполнением memory tool.

Что проверять: Проверять отсутствие разрешения, чужой owner, неверные факты и неприменение прошлых разрешений к новой реплике.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 11, "outgoing": 17, "neighbors": 21, "reverse_reachable": 22, "articulation": false, "scc_size": 22}}

- server/orchestrator.py:30 @ 712f730757ae
- server/orchestrator.py:52 @ 712f730757ae

## Desktop / mini / mobile UI

Режимы показывают Goal-группы, проверенные файлы, заменённые задачи и pending confirmation.

Почему важен: Изменения UI существенны: mini confirmation должна отражать живое состояние раньше бегущих задач.

Условный риск: Старый снимок confirmation в Goal group может скрыть ожидающий ввод или показать лишние команды.

Проверки в исходнике: Представление использует task/goal projection; последние fixes выделяют pending confirmation и краткий mini input.

Что проверять: Проверять desktop/mini/mobile, live confirmation, история superseded и открытие файлов на исходном устройстве.

{"static": {"incoming": 0, "outgoing": 0, "neighbors": 0, "reverse_reachable": 0, "articulation": false, "scc_size": 1}, "semantic": {"incoming": 1, "outgoing": 0, "neighbors": 1, "reverse_reachable": 1, "articulation": false, "scc_size": 1}}

- ui/js/operations.js:1 @ 712f730757ae
- ui/js/mini-operations.js:1 @ 712f730757ae
- ui/css/modes/shared/workspace.03.css:1 @ 712f730757ae
- ui/css/modes/mobile/product-v2.02.css:1 @ 712f730757ae

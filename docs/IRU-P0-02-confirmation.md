# IRU — P0-02: confirmed command truthfulness

Основа проверки: `codex/agentshell-webview`, HEAD `9a62be5059760e32303adfad26fdcb87cf297339`; origin совпадает с HEAD. `IRU-P0-01-audit.md` не найден в доступной рабочей копии/attachments; проверены две гипотезы из задания по актуальному коду.

## Подтверждённая первопричина

В исходном `server/routers/tasks.py:api_confirm_task` обычная ветка без `_pipeline_confirm_future` выполняла `execute_cmd`, затем `ok = not result.get("error")`, выставляла answer="Выполнено." и status="done". Ненулевой returncode, status=failed, unknown/started и отсутствие outcome evidence не проверялись. В baseline HEAD воспроизведено на настоящем handler с fake transport: returncode=7 без error → done/«Выполнено.»; реальных системных действий и LLM calls нет.

`server/controller_non_pipeline.py` при CONFIRM_REQUIRED выбрасывает `ConfirmationRequired`. `server/task_runtime.py:run_on_device` ловит исключение, обычный run_nl_task возвращается со status=confirm. Локальные messages/tool_call ID/iteration/budget/controller frame не сохраняются как continuation. Confirm handler исполнял одну команду отдельной coroutine и не вызывал исходный controller.

Вывод аудитора ограничен обычной веткой **без живого future**. PLAN/broadcast и Browser Bridge используют `_pipeline_confirm_future`: /confirm возвращает решение ожидающей coroutine и не объявляет задачу завершённой. Orphaned PLAN остаётся HTTP 409. Эти ветки не изменены.

## Минимальное исправление

Переиспользованы текущие confirmations, `execute_cmd_result_is_ok`, `execute_cmd_result_is_negative`, outcome markers и `run_journal` validation/basis. Новый `confirmed_command_outcome` различает success, failed, unknown. Success требует returncode=0 и существующего OK outcome contract; explicit failed/error, отрицательный outcome и ненулевой returncode исключают успех. Unknown/started, missing evidence, malformed result, launch_requested или contradictory completion state не становятся доказанным успехом. terminal_sufficient без evidence не принимается.

Command outcome отделён от **goal completion**. Для этой уже размотанной ordinary ветки:

- verified command success → task status=blocked, receipt task_status=partial, command_outcome=success;
- failed execution → failed;
- unknown execution/transport outcome → blocked/partial, без повторного dispatch;
- goal_completed=false и continuation_status=unavailable во всех трёх случаях.

Ответ прямо говорит, что последующие шаги не выполнялись и вся цель не подтверждена. Доказанный успех относится только к одной команде. Не выполняется перезапуск run_nl_task и не добавляется LLM-вызов. Восстановление controller context и completion criteria после unwinding — отдельное решение за рамками этого fix-pass. Успешное выполнение подтверждённой команды поэтому консервативно остаётся частичным результатом даже для запроса, который фактически мог быть одношаговым.

Approval атомарно потребляется до schedule/await; повторный confirm отклоняется. Params фиксируются deepcopy. Callback связан с исходным task object и execution token; удалённая/заменённая task не исполняется. Проверяются cancellation до dispatch, expiry по существующему TASK_TTL, точная executable command и owned/live target. Нет fallback устройства. В transport передаётся текущий user_id; остальные path/security/autonomous guards сохраняются. Если cancel приходит после dispatch, итог не перезаписывается в done.

Preview-only и неоднозначные typed confirmations без точной execute_cmd params отклоняются HTTP 409 вместо попытки выполнить preview как shell-команду. Raw /confirm не принимает confirmation ID: эта legacy ветка одноразовая и не создаёт следующую approval в той же task. /command-decision сохраняет существующую ID/voice/user binding. PLAN confirmation/revision не менялись.

Новая command journal entry содержит только returncode/outcome/status/marker и длины stdout/stderr. Нет literal команды, params, выводов или exception text. Outcome проверяется по полному result до создания sanitized journal entry. Terminal answer проходит существующую basis/self_check validation. UI, модель, agent sources, Context Engine, Android и Browser Bridge не изменены.

## Проверка

`tests/test_confirmed_command_outcome.py`: fake agent с production transport и реальными HTTP endpoints. Покрыты success/failed/unknown, returncode/status/error, launch-only, concurrent/repeated approval, cancel до и после approval, denial/stale ID/expiry, disconnected/unknown/foreign target, чужая task, removed callback, immutable approved params, multi-action original goal и отсутствие credentials в новом journal/logs. Существующие confirmation/cancel/PLAN revision/device isolation тесты запускаются вместе с новыми.

Подтверждение возвращает status=ok как ACK принятия approval, **не** как результат исполнения. Итог доступен через существующий task polling.

## Ограничения

- Ordinary controller loop после ConfirmationRequired не возобновляется: блокер теперь явно виден, а не скрыт сообщением «Выполнено».
- At-most-once обеспечивается в пределах текущей task/confirmation. Task storage остаётся in-memory; restart не восстанавливает эту operation и не запускает её заново.
- Transport exception после возможного dispatch означает unknown. Отмена уже отправленного действия не гарантирует откат; неизвестное действие автоматически не повторяется.
- Fake tests не подтверждают состояние пользовательского ПК и не выполняют реальные OS commands.
- Общий pytest имеет известный pre-existing collection conflict `agent.py` vs `agent.shell`: `test_agent_shell_config.py` и `test_agent_shell_tray.py` проверяются отдельно.

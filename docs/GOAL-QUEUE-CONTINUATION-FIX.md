# Goal Queue: исправление продолжения после partial

Исправления находятся в рабочей копии `codex/goal-task-queue` поверх `cb2d545`.
На сервер в этом проходе не публиковались; существующая production-очередь не изменялась.

- `server/controller_onboarding.py`: блок назначенного результата добавляется после
  построения strict Worker prompt и больше не теряется. Аудитор проверяет его objective.
- `server/routers/tasks.py`: server-only поручение не наследует устройство из UI;
  смешанная цель допускает устройства только при явных подмножествах всех поручений.
- `server/orchestrator.py`: явный `replaces_source_task_id` для нового самостоятельного
  исследования, различие контекста/зависимости и текущие связи задач в контексте.
- `server/worker_scheduler.py`: завершённый неуспешный источник не принимается новой
  обязательной зависимостью. По явному решению Orchestrator новое server research
  заменяет partial/failed источник в ещё не запущенных потребителях той же цели.
  Проверки goal revision, owner/chat и dispatch marker выполняются в одной транзакции.
  Изменения связей сохраняются в task revisions и истории replacement; старый отчёт
  не переписывается. Unknown, running и устройства таким способом не переисполняются.
  Старый partial остаётся историей; цель оценивается по заменяющему исследованию и
  остальным обязательным результатам, без ложного успеха при незавершённой замене.

## Только критические тесты

Запущены 31 тест: **31 passed**, 1 прежнее предупреждение Starlette/httpx.
Лог: `tests_tmp/continuation-critical-final2.log` (игнорируемый локальный артефакт).

```powershell
& $queuePython -m pytest tests/test_goal_continuation_critical.py tests/test_goal_restart_recovery.py tests/test_goal_unified_intake.py tests/test_goal_result_storage.py::test_batch_admission_is_idempotent_and_waiting_does_not_execute -q --tb=short
```

Новый набор воспроизводит потерю assignment, server/device scope, самостоятельную
замену исследования, полное разблокирование существующего потребителя и идемпотентность.
Отрицательные случаи проверяют stale revision, dispatch marker=0, unknown источник,
чужую цель, зависимый repair, device repair и неявные устройства смешанного поручения.
Связанный старый тест теперь требует отказа для terminal-failed dependency и сохраняет
проверки независимого дополнения/повторного request ID.

Полный suite, браузерные тесты и LIVE не запускались. Эти результаты не доказывают,
что реальная модель выберет replacement при следующей реплике. Старые зависшие строки
production-БД автоматически не исправлялись; новый механизм требует явного нового
поручения через Orchestrator после публикации. Внешний HTTP 429 поиска остаётся
внешним ограничением и не превращается в успешное исследование.

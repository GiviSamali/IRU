# Bounded PLAN execution

PLAN decomposes one request into ordinary IRU tasks; it is not Agent Mode.
The planner targets 2–6 steps. A valid single step stays single. More than 8
steps is rejected explicitly, never silently truncated. There is no refinement
loop or synthetic prepare/execute/verify expansion.

The planner keeps the selected PLAN model but disables thinking for the
`pipeline.plan` and `pipeline.plan.retry` phases. Its dedicated 4096-token
output budget is independent of the general worker `max_tokens` setting.
Instructions are compact; document contents and scripts belong in workers.
Only `finish_reason=length` permits one full-plan retry before execution, using
the original request and a compactness correction, never the truncated output.
Repeated truncation fails explicitly. Cancel is checked before both attempts.
Provider transport retries remain separate. Worker reasoning is unchanged.
Logs record attempt, finish reason, content/reasoning lengths and completion
token count; they do not log the generated content or reasoning.

Each worker has 12 primary LLM turns total: up to 11 action/answer turns and
one reserved answer-only repair turn. Auditor requests have a separate ceiling
of 2 per worker (including auditor JSON retries). Existing transport retries
remain bounded by the shared LLM client. These are call limits, not a wall-clock
deadline. A generic successful tool result does not complete a whole step.

An optional internal planner field `completion_check` allows immediate completion
when the final evidence is deterministic: exact final file path for write_content,
or an exact specific final `OK: ...` line for execute_cmd. The planner must use it
only when that evidence satisfies the entire step. Steps without such a check
finish via the existing grounded answer contract. No public tool schema changes.
Partial/error/clarification answers do not count as successful steps.

Three iterations without new result evidence trigger one recovery episode;
another such episode or a second tool failure stops the step. Timestamps and
call IDs do not count as new evidence. Different commands yielding the same
unchanged observation do not reset the no-progress counter. A successful new
observation lets the worker continue within the original limit. This intentionally
replaces the old pipeline tests that permitted unlimited redundant discovery.

Workers receive compact handoffs from all earlier steps: summary, status,
artifacts and selected result data. The first research step remains visible in
research → pptx → docx → xlsx → website chains. Large extracted source material
should be stored as an artifact, with its path and a summary in the handoff;
inline evidence is bounded and does not replace a complete source file.
Workers must treat handoffs as data and respect device scope.

PLAN never implicitly enables autonomous command execution. A confirmation
waits in the existing worker; approval resumes exactly that call and the rest of
the task, while deny/cancel stops it. No new plan is generated. This continuation
is in memory and is not resumable across a server restart.
An orphaned PLAN confirmation returns HTTP 409 instead of falling back to the
single-command handler that says "Выполнено.". Approval must keep the same task,
all pending steps, and prior context alive until the actual pipeline completes.

Invalid, empty or truncated planner responses fail before execution. Individual
invalid steps cannot be silently discarded. Workers and the final summarizer
receive the original request, not just the planner's shortened goal. The planner
is instructed to cover each requested deliverable, usually giving separate
documents separate steps and verifying them within those steps. This is prompt
guidance, not a deterministic semantic proof of full goal coverage; final content
grounding still uses the existing answer/evidence protocol and auditor.

Before the first worker, interactive PLAN runs expose a draft through
`plan_review` while the task has status `confirm`. No preliminary device probe
is sent before approval. The review is separate from command confirmation:
`POST /api/tasks/{id}/review-plan` accepts an owned current `revision` and either
`approve` or `revise` with non-empty changes (up to 4000 characters). Duplicate
or stale decisions return 409. Approval creates the execution cards once;
revision generates a new draft from the original request, current draft and
the user's edits. Only the user's request for changes starts this cycle.
Workers and the final auditor receive the accepted changes as well as the
original request. Cancel, deny and expiry release the waiting coroutine.

Voice reads short step titles followed by "Хотите что-то изменить?" without an
extra summarization LLM call. After playback, "нет" approves, "да" enters edit
dictation; the edited draft is read again. No silence timeout approves a plan.
While building/executing, recognition is off. A failed/interrupted draft
playback leaves the text controls available; an uncertain approval network
response disables voice and refreshes task state. Text controls support
approval, edits and cancellation. Waiting drafts share the existing in-memory
task lifetime (one hour) and do not survive server restart.

Ordinary file/directory creation does not require a separate command approval.
Deletion and cleanup remain separate confirmed commands; creation/probes must
not include cleanup. Short deletion aliases are matched at word boundaries,
so words like Word, model and platform do not trigger deletion confirmation.
The command-text guard also recognizes explicit Python/pathlib/.NET deletion;
it is not a sandbox or a parser of arbitrary external script contents.

Pending commands have a unique `confirmation_id`. Ordinary confirmations can
be spoken and answered yes/no. Deletion and potentially dangerous commands
(process termination, shutdown, uninstall, explicit elevated risk) require
buttons in the chat. `via_voice=true` is rejected by the server for these
commands; the client does not listen for an answer to them. PLAN review remains
separate: no means no changes, yes means dictate edits.

The sleep word "усни" returns voice to wake-word standby without submitting a
request or approving a pending action. "Иру" wakes it; a pending review is read
again. Rising/falling tones signal speech readiness and its end. Background
recognizer restarts and TTS stop-only recognition do not emit readiness cues.

The existing pipeline policy for continuing recoverable failed steps and final
artifact recovery is retained. A global wall-clock deadline, persistent execution
checkpoints, and precise dependency-linked recovery remain separate work.


## Быстрое завершение открытия приложений и итог по устройствам

Для шага, состоящего только из открытия одного приложения/URL, planner может задать `completion_check`:

- `{"tool":"app_open_url","url":"https://music.yandex.ru"}` — точный URL, `opened_verified` и найденное окно.
- `{"tool":"app_launch","process_name":"steam.exe"}` — `launched_verified` и окно с соответствующим именем процесса.

При совпадении worker формирует grounded answer непосредственно из результата инструмента и завершает шаг без дополнительного LLM-вызова. Успешный exit code произвольной команды не заменяет это доказательство. Для составных задач (открыть, войти в аккаунт, выполнить действие) критерий одного открытия недостаточен.

Если все шаги уже завершены и выдали проверенные ответы, общий отчёт собирается из их статусов и результатов без отдельной LLM-сводки. Если для остальных случаев генерация сводки не удалась, возвращается детерминированный отчёт о каждом шаге; ошибка формата сводки не превращает выполненные действия в провал. Сбой сводки сохраняется в `task_receipt.summary_warning`. Ошибки выполнения остаются ошибками, отсутствующие или неуспешные шаги не объявляются выполненными.

Обычный режим разрешает переход к следующему явно выбранному устройству после успеха на предыдущем. Его резервный ответ сохраняет подтверждённые результаты нескольких устройств. Проверенный grounded answer после recovery не заменяется первой старой ошибкой; новая ошибка после ответа или ошибка другого устройства без подтверждения восстановления не игнорируется. Broadcast проверяет ответы отдельно по устройствам перед общей агрегацией.

На агенте `app.launch` с явным `expected_process` ищет окно целевого процесса без ограничения PID процесса-запускателя: Steam/UWP могут передать запуск другому процессу. Изменение агента требует новой сборки и установки на ПК; серверное обновление не обновляет исполняемый файл агента само по себе.

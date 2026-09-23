# Bounded PLAN execution

PLAN decomposes one request into ordinary IRU tasks; it is not Agent Mode.
The planner targets 2–6 steps. A valid single step stays single. More than 8
steps is rejected explicitly, never silently truncated. There is no refinement
loop or synthetic prepare/execute/verify expansion.

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

The existing pipeline policy for continuing recoverable failed steps and final
artifact recovery is retained. A global wall-clock deadline, persistent execution
checkpoints, and precise dependency-linked recovery remain separate work.

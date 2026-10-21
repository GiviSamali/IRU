# Goal Queue acceptance ledger

Baseline: `65e6a7d`; branch: `codex/goal-task-queue`.
The user subsequently authorized Codex to run automated tests and fix failures.
LIVE acceptance remains with the user; see GOAL-TASK-QUEUE-TEST-RESULTS.md.
Syntax parsing is not test execution, migration validation or a LIVE check.

| Requirement | Current source evidence | Remaining acceptance work |
| --- | --- | --- |
| One semantic intake, no keyword-driven delegation | Unified Orchestrator intake, request-bound semantic memory storage, semantic window scope/effects; Browser Bridge already uses tool selection | Finish guard call-site audit and migrate direct memory/window regressions; preserve dangerous-action policy. Real paraphrase/negation quality is unverified. |
| Goal / Task / Artifact / Dependency | Existing Scheduler tables, immutable snapshots, goal projection | Review additive migrations and historical readability on a copied database. |
| Independent deliverable decomposition | `Decision.assignments`, local DAG validation | Complete natural-language scenario coverage; mocks cannot prove model understanding. |
| Dependency readiness and FIFO | Atomic batch admission, pinned result readiness, ready-job claims | Run FIFO, cyclic graph, scope, quota rollback and failure/isolation scenarios. |
| One version of shared material | Full snapshots, SHA-256, inline/full-page transport, contiguous read guard | Run byte-for-byte reconstruction, wrong-source/hash/device checks and real shared research/documents. |
| Multiple Workers | Configurable capacity 1-4, default 1, SQLite triggers, device reservation | Run parallel and conflict checks. Same-device document tasks remain serialized; the requested future same-PC parallel example is not established. |
| Single execution loop | Common Worker tools, classifier/pipeline registry removed; executor/review/step-control modules deleted after test-source migration | Run migrated regressions and review remaining compatibility references. |
| Complete PLAN removal | Active route, review UI/voice states, tracking schemas and old engine removed; retired endpoints return 410 | Finish residual compatibility/UI audit. Historical report schemas and subscription plans remain supported. |
| Add / amend / cancel / status | Goal extension CAS, task amendment CAS, cancellation and goal API/dialogue | Verify race boundaries, versioned metadata, latest-reference selection and user interactions. |
| Security / restart / unknown outcome | Original authority retained, confirmation nonce unchanged, durable dispatch marker, conservative recovery | Run copied-DB restart, lost-coroutine, transport unknown, prompt-injection, scope and resource tests. |
| UI and voice | Existing operations details show goal/dependencies; all member IDs are watched | Verify layout, state/history retention, voice completion ordering, confirmations and Android/Windows behavior. |
| Tests / performance / upgrade deliverables | New tests and read-only metrics exporter prepared | Finish legacy test migration, deliver final commands/inventory/upgrade notes, receive user-run results. No before/after measurements or LIVE Office results exist yet. |

## Required scenarios A-J

| Scenario | Prepared evidence | Acceptance gap |
| --- | --- | --- |
| A: one result | Default single-assignment admission | Run end-to-end text-file scenario. |
| B: three independent results | Rewritten former PLAN-review suite checks three independent members | Run tests and verify three real files. |
| C: shared research with DOCX/PPTX/XLSX | DAG, frozen material and synthetic consumers | Real document creation and common factual basis remain unverified. |
| D: paraphrases/context references | Decision schema/instructions and mock decision transport checks | Real semantic behavior must be checked; complete relevant integration coverage. |
| E: negation/discussion | Orchestrator conversation/clarify policy | Verify no admission for negative/discussion examples; do not infer semantics from mocked outputs. |
| F: failed source | Waiting consumer and no-snapshot coverage | Run; dependent tasks must never fabricate source data. |
| G: two Workers | Parallel synthetic consumers share one hash; exclusive-device case | Run, then perform authorized device isolation checks before increasing production capacity. |
| H: extend goal | Goal revision/source membership/idempotency coverage | Run contextual follow-up after actual Word creation. |
| I: cancel/restart | Queue cancellation, restart and orphan-coroutine tests prepared | Run; interrupted actions must remain unknown and not replay. |
| J: current tools/conversation | Existing regressions partly migrated | Finish mixed legacy migration; run regression/full suites and real Office creation without requiring Microsoft Office. |

## Release boundary

Delegation presentation no longer accepts/rejects model-generated handoff speech
through a completion-word regex. A newly admitted task is represented by actual
task IDs/queue state; optional model speech at that boundary is not reused as an
outcome announcement. Conversation speech and Worker result speech keep their
existing paths. The prepared delegation test now varies Russian/English completion
claims and ordinary progress wording without depending on marker words. Syntax
only; voice tests and TTS have not been run.

Prepared FIFO coverage distinguishes readiness from admission order: successful
source material unlocks consumers in row/admission order relative to independent
work; a failed source leaves both consumers waiting while independent work runs.
Metrics review also fixed missing token values being counted as zeros. Each token
sum is null unless every included event has a valid count; completeness is explicit.
Prepared exporter cases cover missing/partial/zero/negative usage and byte-preserving
read-only access. These tests and measurements have not been run.

Prepared quota-boundary coverage now requests three independent results with zero,
one, two or exactly three daily slots remaining. Rejection must roll back quota,
goal/job/member/request rows, assistant messages and in-memory admission. Exact-fit
admission spends three slots once; retrying the same request returns the same
members without another charge or runner notification. The test stubs scheduling
and executes no device/model work. It has not been run.

Connected window-input fixtures now explicitly carry semantic window scope and
the activate effect through admission. The HTTP continuation case accepts both
legacy orchestrate flag values through the same intake and expects idempotent
reuse of the same question/request binding without replay. Removed phrase-router
expectations from the switch-wording case; choice/identity/staleness/restart
boundaries remain in the connected suite. Syntax only; no tests or native APIs run.

No-device authenticated memory intake now has explicit local conversation/delegate
fixtures, current-request memory authorization, returned Worker-ID tracking and a
real terminal tool payload. Discussion admits no Worker. The synthetic executable
case intentionally ends partial instead of asserting unaudited whole-goal success;
owner isolation and actual memory effects remain asserted. A new memory-authority
assertion was corrected to the database's `fact_text` field. Syntax only; tests and
provider/browser/device execution have not occurred.

HTTP confirmation/suggested-memory fixtures now mock the semantic intake locally
per scenario, provide an online synthetic device, and follow returned Worker IDs
rather than treating the Orchestrator turn ID as the executing task. No global
canned router was introduced. Fixtures without an accepted terminal report expect
unknown instead of fabricating success; suggestion decline and no unsolicited
memory-write assertions remain. These tests have not been run.

Active-task context now prioritizes running/confirmation/input and queued/dependency
work before recent terminal history. More importantly, semantic latest/unique/active
references are resolved against owned current-chat SQLite state rather than the
model's bounded task list. A truncated context cannot make two active jobs look
unique and authorize cancellation of one by accident. Prepared cases cover terminal
history displacement and ambiguity despite a one-job model context. Syntax only;
these integration tests have not been run.

Routing-context review found that sibling task objectives still exposed the same
original multi-result request. Context now carries each proposed assignment (and
an explicit truncation marker), so semantic cancel/amend/status selection can
distinguish Word, slides and research while retaining task/goal revisions. Stored
reports with mismatched task identity or current state are omitted rather than
presented as stale success. A prepared integration case covers siblings, amendment
revision propagation and stale-success suppression. Syntax only; no tests run.

File-confirmation coverage now distinguishes an ordinary live continuation frame
from legacy single-command confirmation without enabling PLAN in either path.
Owner/nonce/duplicate/voice-danger checks remain; the legacy dispatch assertion
also requires the exact device, parameters and a real confirmation object.
Active runtime history persistence expects ordinary mode, while historical PLAN
card fixtures remain readable. Remaining literal true pipeline flags in Python
tests are controlled-rejection cases or historical data. Syntax checks only.

Subscription-limit coverage remains separate from the removed execution mode.
The paid-plan legacy launch case now requires HTTP 410 and unchanged daily usage.
Ordinary runtime receipt/status tests no longer set pipeline mode. Model selection
also rejects the obsolete pipeline flag instead of silently choosing Pro; configured
ordinary/autonomous selection remains. Prepared model tests reflect this boundary.
Syntax checks only; tariff/runtime/model tests have not been run.

Additional legacy expectations were found outside the retired-engine import audit.
Voice PLAN button/source/admin scenarios now require HTTP 410 with no admission,
unchanged historical offer and no newly synthesized launch consent. Historical
review speech revisions are unsupported without TTS. The original-authority test
uses ordinary/autonomous modes, a retired flag explicitly fails without admission,
and stale-nonce continuation uses ordinary command confirmation. Common prompts no
longer name removed tracking tools/markers. These tests remain unexecuted; the
remaining compatibility suites still require source review before full-suite handoff.

Browser regression migration now treats historical PLAN review cards as readable
but non-executable: no approve/revise/command buttons are offered for that schema.
A prepared goal-details case covers related-task count, an explicit source
dependency and waiting state when research failed. The main operation card now
labels dependency waiting explicitly; polling retains goal ID and assignment
revision in message metadata. JS syntax only; browser tests have not been run.

Client polling now reads authoritative task state before applying the execution
timeout. A long browser suspension cannot label a still-waiting dependency or an
already terminal result as a client timeout. Completion outside the visible chat
also releases voice task tracking. Two prepared browser cases simulate the clock
gap and chat switch with fake API/voice objects; neither has been run. JS syntax
checks passed for the edited client and browser-test sources.

Direct window-controller regressions have been migrated from phrase shortcuts to
explicit trusted scope/effects and mocked Worker tool selection. Minimize, maximize,
restore, two native effects, shell rejection, forbidden effects, compact prompt,
opaque same-device references and missing-app behavior remain covered. Ambiguous
references and unsupported virtual desktops use explicit terminal clarification/
failure with no dispatch. No fixture infers the chosen action from request words;
this does not prove semantic/voice quality. Syntax parsing only; no test runs.

Goal-extension source review confirms owner/chat/goal/device checks precede
admission writes. Added prepared cases for each mismatched boundary, requiring no
new job, request binding or goal revision after rejection. Idempotency coverage now
also retries the original request after a later extension: it must return only its
original members, while reuse with changed human authority must conflict without
changing the goal revision. These tests are source-only and have not been run.

Pending amendment now checks the durable dispatch marker as well as queue state
and CAS revision. Cancellation of a contradictory marked queue row preserves the
unknown outcome and command evidence instead of clearing the journal and claiming
that execution never began. The cancel API returns the Scheduler's actual outcome,
not an unconditional cancelled response. Prepared zero/nonzero-marker cases cover
amendment rejection with unchanged payload/revision history and cancellation with
preserved evidence. Tests remain unexecuted.

Restart review strengthened the dispatch boundary: marker presence, not its
truthiness, now forbids redispatch. A stored `dispatch_started_at` value of zero
or a contradictory queued/waiting_dependency row cannot be treated as proof that
execution never began. Recovery projects these cases as unknown; the normal
runner and pre-execution transaction enforce the same rule. Prepared restart and
missing-coroutine cases now include both zero/nonzero markers and queue-state
contradictions. Independent undispatched work remains eligible. No tests run.

Pinned-result review found and closed an empty-join bypass: reading dependency
material now requires one immutable result for every stored edge. A null/missing
result cannot silently disappear from the join and become an empty material list.
For goal consumers the requested device scope must also equal the persisted task
scope before reading any source. Prepared regressions cover unresolved edges and
consumer scope expansion. Syntax checks only; no execution of these tests.

Runtime no longer selects a pipeline controller, skips common initialization for
pipeline mode, or exempts newly produced pipeline receipts from common answer
handling. The live SUGGEST_PLAN scrub branch and client plan-suggestion fallback
have been removed. The old Python-probe keyword hint is inert: mentioning Python,
pip, numpy or a .py name cannot trigger an implicit device command before the Worker
chooses tools. A prepared no-dispatch regression covers these mentions and negative/
discussion requests; explicit runtime discovery remains available through existing
tools. Historical plan-review fields still reject invalid command-confirmation
cards. These source changes are not runtime acceptance evidence.

The final mixed P0 suite now runs against the common Worker (when the user runs
it). The common loop preserves one bounded DeepSeek truncation recovery, discards
every truncated tool call, records token diagnostics and provider unavailability,
keeps the configured output budget, and caps write chunks at 4000 characters
without mutating shared tool schemas. Historical PLAN receipt schemas are fixture
data for SQLite/history compatibility, not calls to a legacy receipt builder.
Source references to the old executor/review/step-control modules are absent from
server/tests/tools. Those three modules have been deleted after this migration.
The archived P0-03 baseline harness has an early explanatory guard for a checkout
without PLAN. The obsolete SUGGEST_PLAN execution guard is removed from the common
Worker. Python AST parsing passed for 63 changed/new files; test and LIVE gates
remain entirely unexecuted. Source consistency, residual guard/UI audit and final
upgrade instructions still require completion before handing over all checks.

The mixed Tool-Only suite no longer imports the retired PLAN modules. Its former
PLAN cases now target goal-member cancellation, write/open/verify ordering, a
single accepted terminal answer without final synthesis, common answer-only repair,
and an unconfirmed transport timeout with the original error intact. Common test
helpers use the actual common tool schemas and explicit request-bound memory
authorization; the positive memory test supplies its current-human proof rather
than deriving authority from words. The remaining Tool-Only regressions are retained.
Source syntax and diff whitespace checks passed; no tests have been executed.

The old controller-pipeline budget/context suite now calls the common Worker and
runtime prompt builder. Prepared cases cover the iteration boundary, ordinary
command dispatch, no budget rejection of explicitly repeated launch variants,
and refusal of an out-of-assignment device rather than silent retargeting. Prompt
and actual model-message captures exclude the other device's paths, GUID and
output; prior Worker history is filtered through the current worker-context path.
Missing/foreign/offline assignment admission is covered by the migrated device
isolation and unified-intake suites. Shared legacy-shaped test data helpers remain
temporarily for the last mixed P0 suite; they import no retired engine. Syntax and
whitespace checks only; no test runs.

The former PLAN recovery-receipt suite no longer imports its executor. Prepared
common Worker cases preserve structured dependency errors and later command
observations without promoting marker output to whole-goal success, exercise the
advertised runtime tool through its typed device API, and retain exact prior file
paths as context rather than current-run evidence. The shared answer-call helper
remains available to action-confirmation/transport suites. PLAN-specific receipt
wrappers and phrase-based conversation-answer rewriting are no longer acceptance
contracts; interpreter verification/rewriting remains covered by the existing
`test_python_toolchain.py` suite. Source syntax and whitespace checks only; none of
these tests have been run.

The former PLAN stability suite now targets the common Worker iteration boundary,
answer-only repair, repeated observations/failures, bounded auditor rejection,
and the fact that stdout markers do not end a larger assignment. Its immutable
source test prepares full material over 20k characters, three consumers, identical
hashes and preserved data-only trust. The existing runtime confirmation scenario
uses ordinary autonomous mode without the retired pipeline flag. PLAN-specific
step counts, completion-check substring matching and truncated handoff semantics
have been replaced by graph bounds, WorkerReport truth and full pinned material.
All these cases remain unexecuted; syntax parsing and diff whitespace checks only.

Multi-device completion regressions now use the common Worker and durable Goal
projection rather than PLAN completion checks or final synthesis. Prepared cases
retain both member results, preserve a failed device's actual command evidence,
and deny aggregate completion when any member fails or lacks confirmed terminal
evidence. A successful application observation alone does not complete a wider
goal. Existing recovered-answer and per-device fallback regressions remain.
These cases are synthetic and unexecuted; only source syntax/whitespace checks
have been performed.

Former PLAN confirmation-continuation tests now prepare a real goal admission
and common Worker/runtime confirmation loop with synthetic transport/model replies.
The cases cover approve/deny/cancel, retained controller frame, duplicate nonce
rejection, no replay of the preceding read, independent queue progress, and a
dependent result that stays waiting when the source is unconfirmed. Legacy orphan
PLAN confirmation expects controlled HTTP 410. Invalid DAGs are rejected rather
than reduced to a smaller assignment. These rewritten tests have only been parsed
for syntax and have not been executed.

Memory-authority regressions no longer import the PLAN executor or another test
module that imports it. The prepared Scheduler-to-Worker cases cover admission
with/without semantic authorization and pending amendment that revokes or grants
fresh authorization. Injection through assignment, shared flags, history and tool
output remains covered. The action-specific deletion case now establishes a real
remember permission before asserting that deletion is still denied. Former PLAN
execution-freedom cases now exercise common Worker correction turns and independent
assignment graphs; the retired three-recovery counter is not an acceptance rule
for the new Worker. Existing common command-budget tests retain bounded-loop
coverage. Syntax parsing only has been performed on these rewritten sources;
no runtime pass is claimed.

Device-isolation and browser-integration suites now have no PLAN-executor imports.
Device tests cover rejection before admission, durable inherited/explicit/server-only
device scope, foreign-owner reads, and atomic rejection of malformed scopes. Existing
broadcast/runtime isolation cases remain. Browser goal-member cases retain the
original human authority despite a conflicting assignment, clarification without
dispatch, and bounded terminal repair with an honest partial result on failure.
The stdout-marker integrity test also no longer depends on PLAN completion checks;
it continues to assert common tool-evidence and WorkerReport boundaries. These are
source changes awaiting user-run tests, not verified runtime results.

Additional source migration: command dependency-error observations and web-search
success/provider-error observations now exercise the common Worker both directly
and with trusted goal-member metadata. The command-budget integration cases use
the same common Worker and retain their dispatch-count/no-budget-block assertions;
they do not assert goal success from command return codes. These three files no
longer import the PLAN executor. Python AST parsing succeeded for these files;
their tests have not been run. Other legacy executor imports still require migration
before the obsolete modules can be removed.

No deployment, merge, force-push or production database modification is authorized.
Keep capacity at one for first deployment. The original objective remains open
until all implementation requirements and applicable user-run verification gates
are satisfied; this ledger is not a reduction of that objective.

## Final source syntax pass

Python AST parsing passed for 76 changed/new Python files; Node `--check` passed for 13 changed/new JavaScript files. `git diff --check` passed. These commands did not execute tests or import application modules.

Historical PLAN metadata no longer creates action blocks. Voice tests now cover absence of retired review/edit APIs and absence of consent/speech from historical offers; ordinary command confirmation and wake/sleep checks remain. Browser source tests require historical reports to remain readable without retired controls or writes. None of these tests has been run.

## Dependency transport source audit

Reviewed current `worker_scheduler.pinned_results`, `_run_job`, `task_summary.get_last_run_summary`, `controller_shared.pinned_material_pending`, and the common Worker call boundary. Snapshot access checks persisted consumer membership, owner/chat, source identity, SHA-256 and device scope. Missing dependency rows raise rather than becoming an empty source list. Large snapshots expose the existing paged tool; only contiguous reads advance consumption. Pending material prevents other tool actions and a successful terminal answer; partial/error reports remain possible. This is source evidence, not execution evidence.

Remaining acceptance requires user-run regression output, copied-database migration/restart checks, real intent scenarios, document contents, UI/voice observations and comparable before/after performance records. No test, LIVE, document-generation or performance result is inferred from source parsing.

## Authorized automated test acceptance — 2026-10-21

The user authorized Codex to run automated tests and fix failures. Final full Python
suite: 1550 passed, 18 existing WebView2 dependency skips, 1 deprecation warning.
Final JavaScript suite: 228 passed, no failures/skips. Full outputs and test scope
are recorded in GOAL-TASK-QUEUE-TEST-RESULTS.md. Earlier unrun entries above record
historical source-review stages, superseded for automated execution by these results.
LIVE intent quality, real documents/devices, production migration and comparative
performance are still unverified and remain separate acceptance items.

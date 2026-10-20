# P0: Failure Recovery & Intent Continuity

Base: `3ac351974694732fbeb46cac7844e84ebfd6913c`. No UI or agent change.

## Scope and evidence

Original: chat 88, task `59db16fb-119b-444d-bd5a-b7a4ae2d3050`, PLAN 48.
The prior read-only audit found 11 successful searches, an accepted informational
terminal of 3575 characters, then six `finish_reason=length` responses on step 2.
Five responses spent their entire 4095/4096-token completion on reasoning; the
third spent 4033 of 4096. The final saved reason was `no_progress`, not truncation.
The source remains failed; no verified created files were recorded.

These historical findings are not a new verification of the market claims.
The full original report and exact PLAN instructions must be copied before
recovering the original deliverables. Partial audit excerpts are not that copy.
At implementation time SSH closed connections before the read-only export; the
user independently reported inability to connect. The outage cause is unverified.

## Code behavior

* `run_pipeline_worker` discards every truncated response before parsing calls.
  One recovery episode uses the same model, token limit and step call budget.
  On the documented DeepSeek route it disables thinking and requests one small
  complete operation at a time. Completed operations/evidence stay in context.
  No partial arguments or reasoning text are dispatched or treated as results.
  A second truncation stops with `model_output_truncated` and numeric usage
  metrics. Unsupported models fail explicitly rather than pretending to support
  the toggle. An exhausted existing step budget is not extended.
* The same rejection applies to terminal repair and final PLAN synthesis.
  Failed PLANs use saved step results, not a further model call that could
  rewrite failure as whole-goal success.
* Step reasons and bounded result records flow into TaskReceipt; the existing
  WorkerReport projection reads `terminal_reason` into `error_code`. Existing
  scheduler snapshots already persist the receipt, journal and report together.
* Failure presentation reads those structures. It does not manufacture a more
  specific cause for legacy records containing only `no_progress`.
* Accepted informational step terminals can be shown in full. They are source
  material, not proof of every market assertion or of a newly performed action.
* Orchestrator `task_status` can select `result_view=saved_report`; opening and
  continuation use `delegate`, or `clarify` when the object is absent/ambiguous.
  No phrase dictionary or additional intent-model call is introduced.
* Failed sources can supply accepted intermediate material to a separate Worker.
  The existing `system_get_last_run_summary` optionally reads that source in
  4000-character pages. Authenticated owner, current chat and target-device scope
  are checked before content access. Old evidence IDs are not imported as proof
  of a new action, and historical content grants no permissions.

Provider contract: [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
documents `thinking.type=disabled` for Chat Completions. The recovery request
omits `reasoning_effort`, retains the token limit, and is wire-tested with a
mock transport. This is not a claim that a live provider call has passed.

## Original-task recovery procedure — pending source access

1. Read-only export the owned job, original request, PLAN48 and its steps,
   associated messages, accepted terminal and search results. Exclude user
   credentials, other chats and unrelated database rows.
2. Record export SHA256, original row content/status and provenance. Import exact
   source records into an isolated SQLite database with network/device mutation
   disabled. Preserve an immutable copy for comparison.
3. Show the full accepted report through the fixed status path. Verify exact
   equality with the source terminal, no search/agent dispatch and no source-row
   changes. Keep the old job `failed`.
4. Create a distinct continuation with `source_task_ids=[original task id]`.
   Confirm the handoff retains the original goal, completed research, limitations
   and a readable full-report reference. Do not label this resuming the old Worker.
5. Make a claim-by-claim verification list from the real report: source, date,
   observation versus forecast, units, geographic scope and unresolved claims.
   For example, inspect the original claim about “60% of working time” against its
   primary source before reusing it. Do not rerun all 11 searches by default.
6. Prepare missing scenarios, application narrative and current event/platform
   shortlist. Check deadlines, eligibility, submission requirements and sources
   only where needed. Distinguish confirmed facts, hypotheses and proposals.
7. Create documents in a separate isolated output folder using the permitted
   existing file tools; check actual paths, existence, contents and readability.
   Save a manifest with hashes and verification results. No registrations,
   submissions, purchases, deletion or overwrite outside the original permission.
8. Inspect the existing API projections consumed by Smart UI; no UI redesign or
   automated UI tests. Production placement/presentation needs LIVE approval.

## Manual LIVE acceptance — separate approval required

Use an explicitly approved test chat, a distinct continuation task and a new
output folder on the intended device. Do not overwrite the source task or files.

* Read the historical status and ask for the intermediate report: readable cause,
  completed research, remaining work, complete saved text, no repeated searches.
* Ask to open the available result before files exist: no false “opened” claim;
  explain that only text exists and clarify the requested operation if needed.
* Continue the document work: a new task references the source and reuses the
  research. Confirm the old task and historical messages are unchanged.
* Run the original end-to-end deliverable scenario with the approved scope.
  Check all requirements and every produced document on the real target device.
* Ask to open a verified created file: observe the actual tool execution and
  current object/window checks, not a status-only response.
* Check ordinary conversation, cancellation and one dangerous operation stopped
  for exact confirmation. Use deterministic local transport tests for the unknown
  outcome/no-replay invariant, not a destructive production experiment.

LIVE success cannot be inferred from unit tests. A real model may not reproduce
the historical truncation; the deterministic tests separately cover that branch.
Mocked routing tests validate server handling of intent decisions, not semantic
accuracy of the live model.

## Deployment and rollback — not performed

Only the server Python files and tool schema need deployment. No agent rebuild,
browser extension update, database migration or UI bundle change is required.
Historical rows must not be recomputed as a migration.

After separate publication/deployment approval:

1. Recheck current main/server revisions and tracked cleanliness; review the exact
   P0 diff against any intervening server changes. Retain the previous revision.
2. Back up SQLite using SQLite's consistent backup mechanism; record source-task
   row hashes. Wait for active Workers or arrange an approved maintenance window:
   a restart must not silently replay uncertain actions.
3. Publish the reviewed change through the normal branch/main process. Run
   `python3 tools/update_server.py --branch main --check`, then the same command
   with `--restart` after it passes.
4. Verify deployed revision, systemd active/running state, restart count, local
   and public HTTP, and startup errors with sensitive query strings redacted.
5. Run approved manual LIVE acceptance and compare original source-row hashes.

Rollback: stop admitting new test work, inspect any active/uncertain action,
then revert the approved P0 commit(s) via the normal reviewed main workflow and
the guarded updater. Do not blindly reset production or restore the entire old
database: that would erase newer messages/results. The additive receipt fields
and optional tool arguments are backward-compatible with the previous server;
retain newly recorded evidence. Database restoration is only for proven storage
damage, with its own approval and preservation of newer records.

# IRU — Result First Goal Presentation (experimental branch)

## Intent

- Human talks to **Orchestrator** once per turn.
- Orchestrator admits a **Goal** consisting of separate deliverable assignments.
- Workers execute only their assignment; their prose, retries and tool errors are **internal evidence**, not separate chat messages.
- Scheduler / runtime persists per-Task WorkerReport v1, immutable result receipts and approval decisions.
- A deterministic `goal_presentation()` composes the owner-scoped Goal outcome **without another LLM call**. It does not trust raw Worker prose, cannot mint permissions and does not call tools.
- The chat shows one Goal message and voice turn. Operations still provides individual Task details, tool journals and pending confirmations.

## Implemented routing

1. `run_turn()` binds the user's Orchestrator reply to the admitted `goal_id`.
2. New assignments persist `result_first=True` metadata; old history remains compatible.
3. `/api/tasks/{orchestrator_task_id}` projects the current Goal until it is terminal; existing polling and voice now follow **the whole Goal**, not the first Worker.
4. `/api/goals/{goal_id}` exposes a scoped aggregate presentation; `POST /api/goals/{goal_id}/cancel?chat_id=...` stops the owned Goal group.
5. `/api/chats/{chat_id}/messages` hides newly internal Worker messages, projecting one Goal result into the Orchestrator turn even after reopening the chat.
6. Human and Worker routing context filters out the new internal Worker messages. Prior raw journals remain in SQLite and in Operations.
7. `ui/js/chat.js` stops creating one chat/voice bubble per Worker. Goal cancellation uses the Goal endpoint and is recoverable after reload.

## Truth contract

- `success` requires `goal_view.goal_completed` and a successful, frozen result receipt for **every required assignment**. An existing file, raw claim, failed write or unverified path is insufficient.
- A recoverable intermediate error does **not** become the human-facing final answer when the Goal later completes with confirmed evidence.
- `unknown` is never converted into `success`, and no action with unknown outcome is replayed by this presentation layer.
- Files included in the short summary come only from verified WorkerReport artifacts; expected output files are preferred over helper scripts. A file not included in structured evidence cannot be asserted as verified by the presenter.
- `waiting_confirmation` and `waiting_input` are surfaced as needing attention in Operations. Dangerous actions still require their original binding/confirmation.

## Deliberately unchanged

- The Worker's internal `answer.text` tool protocol, Action/Answer Auditor and evidence validation are **retained** for now. Removing them without trustworthy native postcondition verification would reduce reliability. Their output is simply no longer treated as a separate user-facing reply.
- General shell tools cannot guarantee filesystem write isolation between assignments. Assigned-scope instructions and device permissions remain as before.
- Historic Worker messages from before `result_first` remain visible to avoid silently rewriting prior conversations.
- This branch does not run any schema migration, replay unknown commands, merge into main or deploy the server.

## Acceptance

Use a separate Python test environment/SQLite database; no real device needed for the automated checks:

```powershell
python -m pytest tests/test_goal_result_first.py tests/test_goal_decision_contract.py tests/test_goal_result_storage.py tests/test_goal_parallel_scheduler.py tests/test_goal_chat_delete_lifecycle.py -q
node --test tests/chat-recovery-browser.test.cjs
```

Then, if these pass, run `python -m pytest -q` and the full JavaScript suite.

LIVE acceptance (only after review):
- Request DOCX and XLSX from one source file. One human Orchestrator message, two internal Task cards in Operations, one spoken final outcome.
- A recoverable oversized `write_content` followed by a verified XLSX must not generate a final error message.
- If XLSX fails and PPTX succeeds, aggregate status must be partial and not falsely claim both.
- Test Goal cancel, command confirmation, disconnected device and reopening the chat.
- Validate that user-facing output references office files, not temporary helper Python scripts.

**Status:** Code committed on a separate branch. Syntax of modified JS files was parsed in V8; Python/Playwright/full suite and LIVE have not been executed yet.

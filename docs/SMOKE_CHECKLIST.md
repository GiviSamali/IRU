# Smoke Checklist

Use this checklist before deploy, demo, or tester handoff. It is practical manual coverage for the current AgentOS architecture: server orchestrates, local agent owns device state, typed tools are preferred, UI shows used tools and evidence, and final user answers go through `answer.text`.

## Environment

- [ ] Confirm the expected branch/commit is deployed.
- [ ] Confirm no local `dist/`, build artifacts, `.venv`, logs, DB files, or user files are included in the patch.
- [ ] Confirm `server/llm_config.json` or environment configuration exists on the target machine.

## Server Start

Bash:

```bash
python -m py_compile server/main.py
cd server
python main.py
```

PowerShell:

```powershell
python -m py_compile server/main.py
Set-Location server
python main.py
```

Checks:

- [ ] Server starts without import errors.
- [ ] Web UI opens at the configured host, usually `http://localhost:8000`.
- [ ] Login works with the expected token.

## Agent Connect

- [ ] Start the local agent.
- [ ] Agent connects over WebSocket.
- [ ] Device appears in the UI device list.
- [ ] Reconnect the agent once and confirm cached passport data is still sent if available.

## Device Passport

- [ ] Device Passport is visible in the UI.
- [ ] Activation fields are visible.
- [ ] Runtime/Python/pip fields are visible.
- [ ] State snapshot source is visible: `live`, `agent_cache`, or `missing`.
- [ ] GPU/hardware summary appears when available.

## Activation

User input:

```text
Activate this device and show whether it is ready.
```

Checks:

- [ ] Used tools includes `device.activate`.
- [ ] Device Passport activation status updates.
- [ ] Final answer is `answer.text`.
- [ ] Final answer has current-run `basis` referencing the activation step id.
- [ ] No answer claims activation from connection status alone.

## Runtime Prepare

User input:

```text
Prepare the managed Python runtime and report Python and pip status.
```

Checks:

- [ ] Used tools includes `device.prepare_runtime`.
- [ ] Runtime summary includes status and Python/pip details or a clear failure.
- [ ] Final answer basis references the runtime step id.
- [ ] `execute_cmd` is not the preferred path for runtime preparation.

## Refresh State

User input:

```text
Refresh this device state and summarize CPU, RAM, disk, GPU, and uptime.
```

Checks:

- [ ] Used tools includes `device.refresh_state`.
- [ ] Snapshot source is `live` after a successful refresh.
- [ ] Identity mismatch, if any, is reported as mismatch/routing evidence.
- [ ] Final answer basis references the refresh step id.

## Write File

User input:

```text
Create C:\Temp\iru_smoke_note.txt with the text "IRU smoke check OK".
```

Checks:

- [ ] Used tools includes `write_content`.
- [ ] Shell fallback is not used for the text write.
- [ ] Final answer basis references the write step id.
- [ ] Failure is reported if the path cannot be written.

## Launch GUI App

User input:

```text
Launch the PyQt demo app and verify its main window is visible.
```

Checks:

- [ ] Used tools includes `app.launch`.
- [ ] Used tools includes `app.verify_launch`, `window.verify`, or `window.find`.
- [ ] Process launch alone is not treated as GUI success.
- [ ] Final answer basis references both launch and verification evidence when both are used.

## Verify Window

User input:

```text
Check whether Notepad is open.
```

Checks:

- [ ] Used tools includes `window.find` or `window.verify`.
- [ ] Result includes found/not_found and relevant title/process/pid/visibility data when available.
- [ ] Final answer basis references the current window step id.
- [ ] Old chat history is not used as current window evidence.

## Used Tools and Evidence UI

- [ ] Each scenario shows used tools in the UI.
- [ ] Run journal entries include `step_id`, `tool_name`, `status`, `summary`, and `result`.
- [ ] Final answer basis references current-run non-answer step ids.
- [ ] Basis does not use tool names such as `window.find` or `write_content`.

## UI Status Labels

- [ ] Send a normal task and confirm the compact live status uses a known label such as `ИРУ думает...`, `Выполняю инструмент...`, or `Выполняю задачу...`.
- [ ] Inspect or force an unknown backend task status/current-step value and confirm the compact label falls back to `Выполняю задачу...` instead of showing raw text.
- [ ] Confirm final answer text is displayed normally and is not sanitized as a status label.
- [ ] Confirm used tools and command details still show real tool names, summaries, and evidence.

## Tool-Only Protocol Validation Inventory

Inspected files:

- `server/run_journal.py`
- `server/answer_auditor.py`
- `server/controller_non_pipeline.py`
- `server/controller_pipeline.py`
- `tests/test_tool_only_protocol.py`

Current checks found:

- [x] Raw final assistant content is rejected and corrected toward `answer.text`.
- [x] Exactly one tool call per iteration is enforced.
- [x] `answer.text`, `answer.ask_clarification`, `answer.report_failure`, and `answer.request_confirmation` are terminal answer tools in controller handling.
- [x] `grounded_report` requires non-empty `basis`.
- [x] `basis` must reference current-run non-answer `step_id` values, not tool names.
- [x] Stale chat history is not accepted as evidence because basis validation only accepts current run journal steps; the semantic auditor also states that previous chat history is not evidence.

Protocol gaps found during this pass:

- None for the requested checks.

No keyword dictionaries, regex scenario guards, or hardcoded routing based on demo phrases were added.

## Failure Stop Conditions

Stop the demo and report evidence if any of these happen:

- raw assistant text becomes the final user answer;
- more than one tool is accepted in one iteration;
- `grounded_report` succeeds with empty or stale basis;
- basis references a tool name instead of `step_id`;
- file/window/runtime task prefers `execute_cmd` while a typed tool is available;
- UI hides used tools for an action that changed or observed device state.

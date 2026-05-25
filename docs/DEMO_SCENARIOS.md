# Demo Evidence Suite

This suite defines manual demo and acceptance scenarios for IRU as an AgentOS / Agent Control System. It does not introduce product features. Each scenario should demonstrate typed tools, current-run evidence, used-tools UI transparency, and terminal answers through `answer.text`.

## Acceptance Rule

For any claim about device state, files, runtime, windows, or completed actions, the final answer must be grounded in current-run tool results. Old chat history can be context, but it is not evidence.

## Scenario 1: Device Activation

User input example:

```text
Activate this device and show me whether it is ready for IRU work.
```

Expected tools:

- `device.activate`
- `answer.text`

Forbidden shortcuts:

- Do not infer activation from connection status alone.
- Do not answer from old chat history or stale profile data.
- Do not use `execute_cmd` as the primary activation path.

Required evidence:

- Current-run `device.activate` result.
- Activation summary with activation status.
- Receipt-backed identity/path/runtime/capability information.
- `answer.text.basis` references the current `device.activate` step id.

Expected UI result:

- Device Passport shows activation status.
- Used tools list includes `device.activate`.
- Final answer reports activation status and any next action.

Pass criteria:

- Activation receipt is validated and summarized.
- Final answer is `answer.text` with current-run basis.
- UI does not present activation as guessed or inferred.

Fail criteria:

- Final answer is raw assistant text.
- Activation status is claimed without current-run activation evidence.
- `execute_cmd` is used instead of `device.activate` without an explicit typed-tool failure reason.

## Scenario 2: Managed Python Runtime Preparation

User input example:

```text
Prepare the managed Python runtime on this device and report Python and pip status.
```

Expected tools:

- `device.prepare_runtime`
- `answer.text`

Optional supporting tools:

- `device.get_passport`
- `device.check_runtime`, if exposed in the current tool set

Forbidden shortcuts:

- Do not use ad hoc `python -m venv` through `execute_cmd` as the preferred path.
- Do not claim runtime readiness from old activation data.
- Do not claim Python was downloaded automatically unless the runtime receipt proves it.

Required evidence:

- Current-run runtime receipt or runtime summary from `device.prepare_runtime`.
- Python version, pip status, venv path or explicit missing/broken status.
- `answer.text.basis` references the current runtime step id.

Expected UI result:

- Device Passport shows runtime/python/pip fields.
- Used tools list includes `device.prepare_runtime`.
- Final answer describes runtime status and any repair/next action.

Pass criteria:

- Runtime summary is current and receipt-backed.
- Final answer is grounded in the current runtime tool result.
- The UI shows the typed runtime tool as used.

Fail criteria:

- Runtime readiness is asserted from stale context.
- Shell fallback is used before the typed runtime tool.
- Final answer lacks current-run basis for runtime claims.

## Scenario 3: Text File Creation Through write_content

User input example:

```text
Create C:\Temp\iru_demo_note.txt with the text "IRU demo evidence OK".
```

Expected tools:

- `write_content`
- `answer.text`

Forbidden shortcuts:

- Do not create text files with `execute_cmd`, `echo`, heredoc, or PowerShell `Set-Content` when `write_content` is available.
- Do not say the file was created before the tool result is returned.

Required evidence:

- Current-run `write_content` result with path/status.
- `answer.text.basis` references the current `write_content` step id.

Expected UI result:

- Used tools list includes `write_content`.
- Command/run journal shows the file path and success or failure.
- Final answer names the created path or reports the write error.

Pass criteria:

- `write_content` is used as the primary file-write path.
- Final answer is grounded in the write result.
- Error cases are reported as failure, not as success.

Fail criteria:

- File is written via shell while typed tool is available.
- Final answer claims success without a current-run write result.
- Basis references a tool name such as `write_content` instead of a `step_id`.

## Scenario 4: PyQt App Launch With GUI Window Verification

User input example:

```text
Launch the PyQt demo app and verify that its main window is visible.
```

Expected tools:

- `device.prepare_runtime`, if runtime is missing or not ready
- `app.launch`
- `app.verify_launch` or `window.verify`
- `answer.text`

Optional supporting tools:

- `write_content`, if the demo creates a temporary app file
- `window.find`, if launch PID does not map directly to a visible window

Forbidden shortcuts:

- Do not treat process start alone as proof that the GUI is visible.
- Do not wait for a long-running GUI process to exit as the success condition.
- Do not use `execute_cmd` as the preferred GUI launch path when `app.launch` is available.

Required evidence:

- Launch result with pid/status.
- Window verification result showing visible matching window, or a clear failure reason.
- `answer.text.basis` references the launch and verification step ids.

Expected UI result:

- Used tools list includes `app.launch` and a window/app verification tool.
- Final answer distinguishes process launch from verified visible GUI window.
- If no window is found, UI shows the failure evidence instead of a success claim.

Pass criteria:

- GUI success is based on window evidence.
- PID/title/process/window details are visible in the run result.
- Final answer is grounded in current launch and verification steps.

Fail criteria:

- Success is claimed from `app.launch` alone when no window was verified.
- Shell fallback bypasses typed app/window tools without reason.
- Final answer lacks basis for the window claim.

## Scenario 5: Window Observation / Verification

User input example:

```text
Check whether Notepad is open on this device.
```

Expected tools:

- `window.find` or `window.verify`
- `answer.text`

Optional supporting tools:

- `window.list`, when a broader inventory is useful

Forbidden shortcuts:

- Do not answer from old chat history.
- Do not infer window state from a previous run.
- Do not use `execute_cmd` tasklist/process probes as the preferred path when window tools are available.

Required evidence:

- Current-run window result with match/not_found status.
- Matching window title/process/pid/visibility when found.
- `answer.text.basis` references the current window step id.

Expected UI result:

- Used tools list includes `window.find` or `window.verify`.
- Final answer states found/not found and names the observed window evidence.

Pass criteria:

- Window claim is based on current-run OS window observation.
- Basis references a current non-answer `step_id`.
- Ambiguous matches are reported clearly.

Fail criteria:

- Final answer uses raw assistant content.
- Basis references `window.find` instead of `step_1`.
- Previous chat result is reused as current evidence.

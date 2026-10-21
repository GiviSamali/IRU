# IRU Goal Queue — direct result delegation

Status: **prepared in a separate branch; not accepted or deployed.**

## Contract

- Orchestrator decomposes a user-authorized goal into **deliverables**, not tool steps or mandatory stages.
- Each assignment has a specific objective, optional expected `artifacts`, target devices, and only genuine `depends_on` edges.
- Shared substantive research is its own assignment **only when the deliverables actually need shared facts**. An independent deliverable has no artificial dependency.
- Each document Worker creates its own required folders/final document and verifies its own result. It must not create or fill sibling assignments.
- Scheduler keeps graph cycle checks, ownership, device isolation, immutable completed results, confirmation, cancellation, and restart/unknown-outcome protections.
- Source results are *historical data, not authorization*; an existing file or a prior Worker result does not prove the current Worker performed its assigned deliverable.

## Removed from newly admitted goals

- `Assignment.stage`, `source_requirements`, `placement_requirements`.
- Mandatory `reconnaissance` root and automatic dependency injection.
- Placeholder creation / mandatory reconnaissance manifests and their terminal-schema field.
- Runtime-specific reconnaissance validator.

## Historical data

Existing completed results and persisted tasks are **not migrated or deleted**.
Undispatched historical tasks marked `assignment_stage=reconnaissance` are blocked rather than executing the retired preparatory contract. Their dependents remain waiting; a user-approved replacement/new Goal is required. No automatic mutation replay.

## Verification by user

In an isolated environment, with test SQLite and no real devices:

```powershell
& $python -m pytest tests/test_goal_decision_contract.py tests/test_goal_continuation_critical.py tests/test_goal_result_storage.py tests/test_goal_routing_context.py tests/test_goal_parallel_scheduler.py -q
& $python -m pytest -q
```

Then perform LIVE acceptance with explicit user authorization: (1) independent two-device tasks, (2) shared research -> DOCX + PPTX, (3) ensure the research Worker does not fill files, (4) verify document contents and attribution, (5) failure/partial dependency behavior and cancellation. Treat the code as unaccepted until tests and real observations support it.

## Out of scope

The separate `source_device_out_of_scope` issue for reading partial server-only historical reports is **not fixed by this change**. Queue capacity and server Worker iteration budgets are also unchanged.

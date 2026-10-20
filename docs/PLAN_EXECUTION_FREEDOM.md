# IRU: PLAN recovery freedom (2026-10-20)

- PLAN has 12 ordinary and up to 24 explicitly requested extended turns per
  step, including one final answer repair.
- Up to three *distinct* correction attempts for tool errors are possible;
  repeated identical failures and stagnant results still stop.
- Explicit independent_after_failure=true lets the next independent step
  proceed after ordinary step failure. Confirmation expiry, unknown mutable
  result and failed transfer remain hard stop. Whole PLAN stays partial/failed.
- Word COM TypeText is no longer categorically denied. Confirmations,
  device ownership, path validation and user restrictions remain authoritative.
- Test-only requests must keep all helper scripts inside the permitted folder.
- Windows agent initial .ps1 writes include UTF-8 BOM. Subsequent appends use
  UTF-8 without extra BOM. PowerShell wrapper propagates nested LASTEXITCODE.
- Error presentation includes known reasons and the last failed command type.

IMPORTANT: server deployment alone does not change the installed Windows agent.
Agent changes require a separate rebuild and installation. No destructive
operations or user-device tests performed as part of this repository change.
Manual Office LIVE validation and comparison of source SHA256 remain required.

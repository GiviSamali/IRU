"""Bounded progress and evidence handling for PLAN steps only."""
from __future__ import annotations

import json


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()
                if k not in {"timestamp", "collected_at", "duration_ms", "elapsed_seconds", "step_id", "call_id"}}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


class StepProgress:
    """At most one recovery episode; no new evidence for three turns stops it."""
    def __init__(self):
        self.cursor = 0
        self.seen = set()
        self.stalled = 0
        self.recovery_used = False
        self.recovery_active = False
        self.repeated = 0

    def observe(self, journal):
        entries = journal[self.cursor:]
        self.cursor = len(journal)
        progress = False
        failed = False
        for entry in entries:
            result = entry.get("result") or {}
            if not isinstance(result, dict):
                result = {"value": result}
            failure = (entry.get("status") in {"error", "failed", "blocked"}
                       or result.get("status") in {"failed", "error", "blocked"}
                       or bool(result.get("error")) or result.get("returncode") not in (None, 0, "0"))
            failed = failed or failure
            key = json.dumps(_stable(result), sort_keys=True, ensure_ascii=False, default=str)
            if key in self.seen:
                self.repeated += 1
            elif not failure:
                progress = True
            self.seen.add(key)
        if failed:
            if self.recovery_used:
                return "stop", "recovery_exhausted"
            self.recovery_used = self.recovery_active = True
            self.stalled = 0
            return "recover", "tool_failure"
        self.stalled = 0 if progress else self.stalled + 1
        if progress:
            self.recovery_active = False
        if self.stalled >= 3:
            if self.recovery_used:
                return "stop", "no_progress"
            self.recovery_used = self.recovery_active = True
            self.stalled = 0
            return "recover", "no_progress"
        return "continue", ""


def completion_matches(step, entry):
    """Only an explicit final-step check can trigger deterministic completion.

    Generic OK markers are tool success, not proof of completing the whole step.
    Plans without a check finish through the existing grounded answer contract.
    """
    check = step.get("completion_check")
    result = entry.get("result") or {}
    if not isinstance(check, dict) or not isinstance(result, dict):
        return False
    if result.get("error") or result.get("returncode") not in (None, 0, "0"):
        return False
    if entry.get("status") in {"failed", "error", "blocked"} or result.get("status") in {"failed", "error", "blocked"}:
        return False
    tool = entry.get("action") or entry.get("tool_name")
    if check.get("tool") != tool:
        return False
    path = check.get("path")
    if tool == "write_content":
        return (isinstance(path, str) and bool(path) and result.get("path") == path
                and str(result.get("summary") or "").startswith("OK:"))
    if tool == "execute_cmd":
        marker = check.get("stdout_contains")
        if not isinstance(marker, str) or len(marker.strip()) < 8 or not marker.startswith("OK: "):
            return False
        return any(line.strip() == marker.strip() for line in str(result.get("stdout") or "").splitlines())
    return False


def step_handoff(summary, commands, status):
    """Keep source material and artifacts, not previous worker conversations."""
    evidence = []
    artifacts = []
    for command in commands:
        result = command.get("result")
        if not isinstance(result, dict):
            continue
        for key in ("path", "file_path", "url", "files_verified", "created_files", "artifacts_created"):
            value = result.get(key)
            paths = value if isinstance(value, list) else [value]
            for path in paths:
                if isinstance(path, str) and path and path not in artifacts:
                    artifacts.append(path)
        if command.get("action", "").startswith("answer") or command.get("tool_name", "").startswith("answer."):
            continue
        # Explicit content fields, no opaque stdout histories or private command bodies.
        compact = {key: result[key] for key in ("title", "url", "path", "content", "text", "data", "facts", "results", "summary", "error") if key in result}
        if result.get("stdout"):
            compact["stdout"] = str(result["stdout"])[:2500]
        if compact:
            data = json.dumps(compact, ensure_ascii=False, default=str)
            evidence.append({"tool": command.get("action") or command.get("tool_name"),
                             "status": command.get("status"), "data": data[:4000],
                             "truncated": len(data) > 4000 or len(str(result.get("stdout") or "")) > 2500})
    return {"status": status, "summary": summary[:4000], "artifacts": artifacts[:24], "summary_truncated": len(summary) > 4000,
            "evidence": (evidence[:2] + evidence[-2:] if len(evidence) > 4 else evidence)}

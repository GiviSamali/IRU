"""Bounded PLAN recovery regression tests; never execute device commands."""
from server.pipeline_step_control import StepProgress
from server.controller_pipeline import normalize_pipeline_plan


def observed(command, result, status="success"):
    return {"action": "execute_cmd", "command": command,
            "result": result, "status": status}


def test_two_distinct_failures_can_be_corrected():
    state = StepProgress()
    log = [observed("run-script", {"error": "encoding"}, "error")]
    assert state.observe(log) == ("recover", "tool_failure")
    log.append(observed("validate-script", {"error": "syntax"}, "error"))
    assert state.observe(log) == ("recover", "tool_failure")
    log.append(observed("fix-script", {"returncode": 0, "stdout": "verified"}))
    assert state.observe(log) == ("continue", "")


def test_identical_failure_is_still_bounded():
    state = StepProgress()
    log = [observed("run-script", {"error": "same"}, "error")]
    assert state.observe(log) == ("recover", "tool_failure")
    log.append(observed("run-script", {"error": "same"}, "error"))
    assert state.observe(log) == ("stop", "recovery_exhausted")


def test_three_distinct_failure_recoveries_max():
    state = StepProgress()
    log = []
    for i in range(3):
        log.append(observed(f"run-{i}", {"error": f"error-{i}"}, "error"))
        assert state.observe(log) == ("recover", "tool_failure")
    log.append(observed("run-3", {"error": "error-3"}, "error"))
    assert state.observe(log) == ("stop", "recovery_exhausted")


def test_normalized_plan_keeps_explicit_budget_and_independence():
    plan = normalize_pipeline_plan({"steps": [
        {"title": "Word", "execution_budget": "extended"},
        {"title": "Excel", "independent_after_failure": True},
        {"title": "Report"},
    ]}, "Create docs", "pc")
    assert plan["steps"][0]["execution_budget"] == "extended"
    assert plan["steps"][1]["independent_after_failure"] is True
    assert "execution_budget" not in plan["steps"][2]
    assert "independent_after_failure" not in plan["steps"][2]

"""A mechanically proven action must not need a second model to be called done.

The 30 Oct LIVE case: `project.txt` was replaced natively, read back at the new
version, hashes matched, and the report still said "partial" because content
coverage was graded by an LLM and because the answer admitted it had no
byte-level diff.
"""
import pytest

from server.worker_reports import build_worker_report

ROOT = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE"
OLD = "c662aeb0e6b88588258a7cb7fa2d8cc37d6bd7d3e017e706ed54b5b6efef8cc1"
NEW = "d1b005fa22c2c76579bcb555d7620aa77c6e04ebf54ea0eeb362232a57cb2b61"


def read(step, digest, *, device="givi", text="Название: Работаем\nВерсия: 1\n"):
    return {"task_id": "task-1", "step_id": step, "tool_name": "read_file", "status": "success",
            "device_id": device, "target_device_id": device,
            "result": {"status": "ok", "path": f"{ROOT}/project.txt", "text": text,
                       "sha256": digest, "complete": True}}


def replace(step="step_2"):
    return {"task_id": "task-1", "step_id": step, "tool_name": "write_content", "status": "success",
            "device_id": "givi", "target_device_id": "givi",
            "result": {"status": "ok", "path": f"{ROOT}/project.txt", "verified": True,
                       "committed": True, "file_sha256": NEW, "total_size": 45}}


def answer(step="step_4"):
    return {"task_id": "task-1", "step_id": step, "tool_name": "answer.text", "status": "terminal",
            "result": {"answer_type": "grounded_report", "text": "Готово: Название: Работаем.",
                       "basis": ["step_2", "step_3"],
                       "self_check": {"depends_on_current_external_state": True,
                                      "claims_completed_action": True,
                                      "has_sufficient_evidence": True,
                                      "missing_evidence_question": ""}}}


def task(commands, *, scope="general", artifacts=None):
    return {"task_id": "task-1", "worker_id": "worker-1", "status": "done",
            "device_ids": ["givi"], "execution_scope": scope,
            "assigned_artifacts": artifacts if artifacts is not None
            else ["project.txt в папке IRU-EC01-LIVE с новым названием"],
            "commands": commands, "answer": "Готово: Название: Работаем."}


def test_native_edit_with_readback_is_success_without_second_model():
    report = build_worker_report(task([read("step_1", OLD), replace(), read("step_3", NEW), answer()]))
    assert report["status"] == "success"
    assert report["goal_completed"] is True
    assert report["error_code"] is None
    assert report["artifacts"][0]["content_verified"] is True


def test_readback_of_an_older_version_is_not_content_proof():
    """The same read that proves the new version must match it, not the old one."""
    report = build_worker_report(task([read("step_1", OLD), replace(), read("step_3", OLD), answer()]))
    assert report["status"] != "success"
    assert report["artifacts"][0]["content_verified"] is False


def test_success_still_requires_the_file_effect():
    """A successful read alone never claims the requested change happened."""
    report = build_worker_report(task([read("step_1", OLD), answer()]))
    assert report["status"] != "success"
    assert report["goal_completed"] is False


def test_unknown_outcome_is_still_unknown():
    unknown = replace()
    unknown["result"] = {"status": "unknown", "error": "confirmed_transport_outcome_unknown",
                         "dispatched": True}
    report = build_worker_report(task([read("step_1", OLD), unknown]))
    assert report["status"] == "unknown"
    assert report["goal_completed"] is False


def test_analysis_scope_still_needs_semantic_coverage():
    """Semantic work keeps its meaning check; only mechanical proof is freed."""
    report = build_worker_report(task([read("step_1", OLD), replace(), read("step_3", NEW), answer()],
                                      scope="analysis", artifacts=[]))
    assert report["status"] != "success"


def _answer_with_unresolved(items):
    step = answer()
    step["result"]["unresolved"] = items
    return step


def test_unassigned_extra_failure_does_not_downgrade_verified_result():
    """The 01:48 LIVE case: an extra lite.txt attempt failed, project.txt was done."""
    report = build_worker_report(task(
        [read("step_1", OLD), replace(), read("step_3", NEW),
         _answer_with_unresolved(["Не удалось создать lite.txt: файл не найден"])],
        artifacts=["project.txt в папке IRU-EC01-LIVE с новым названием"]))
    assert report["status"] == "success"
    assert report["goal_completed"] is True


def test_unresolved_about_the_assigned_file_still_downgrades():
    report = build_worker_report(task(
        [read("step_1", OLD), replace(), read("step_3", NEW),
         _answer_with_unresolved(["Не удалось изменить project.txt: файл занят"])],
        artifacts=["project.txt в папке IRU-EC01-LIVE с новым названием"]))
    assert report["status"] == "partial"
    assert report["goal_completed"] is False

"""Free-form presentation: the model narrates, the report keeps the facts.

Canned verdict phrases ("Обязательные файловые результаты не подтверждены",
"Файл записан; подтверждена его целостность...", "Часть поручения выполнить не
удалось") are replaced by the model's own words, while status, error_code and
the artifact SHA stay machine-derived.
"""
import pytest

from server import orchestrator as orch
from server.worker_reports import build_worker_report

ROOT = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE"
OLD = "c662aeb0e6b88588258a7cb7fa2d8cc37d6bd7d3e017e706ed54b5b6efef8cc1"
NEW = "d1b005fa22c2c76579bcb555d7620aa77c6e04ebf54ea0eeb362232a57cb2b61"
MODEL_TEXT = "Название в project.txt изменено на «Работаем», остальное не тронуто."


def read(step, digest):
    return {"task_id": "task-1", "step_id": step, "tool_name": "read_file", "status": "success",
            "device_id": "givi", "target_device_id": "givi",
            "result": {"status": "ok", "path": f"{ROOT}/project.txt", "text": "Название: Работаем",
                       "sha256": digest, "complete": True}}


def replace(step="step_2"):
    return {"task_id": "task-1", "step_id": step, "tool_name": "write_content", "status": "success",
            "device_id": "givi", "target_device_id": "givi",
            "result": {"status": "ok", "path": f"{ROOT}/project.txt", "verified": True,
                       "committed": True, "file_sha256": NEW, "total_size": 45,
                       "replacement": {"mode": "replace", "matches": 1,
                                       "previous_sha256": OLD}}}


def answer(text=MODEL_TEXT, step="step_4"):
    return {"task_id": "task-1", "step_id": step, "tool_name": "answer.text", "status": "terminal",
            "result": {"answer_type": "grounded_report", "text": text, "basis": ["step_2"],
                       "self_check": {"depends_on_current_external_state": True,
                                      "claims_completed_action": True,
                                      "has_sufficient_evidence": True,
                                      "missing_evidence_question": ""}}}


def task(commands, artifacts, answer_text=MODEL_TEXT):
    return {"task_id": "task-1", "worker_id": "worker-1", "status": "done",
            "device_ids": ["givi"], "execution_scope": "general",
            "assigned_artifacts": artifacts, "commands": commands, "answer": answer_text}


def test_free_form_keeps_the_model_words_on_success(monkeypatch):
    monkeypatch.setenv("IRU_FREE_FORM_REPORT", "1")
    report = build_worker_report(task(
        [read("step_1", OLD), replace(), read("step_3", NEW), answer()],
        ["project.txt в папке IRU-EC01-LIVE с новым названием"]))
    assert report["status"] == "success"
    assert MODEL_TEXT in report["summary"]
    assert "Обязательные результаты подтверждены" not in report["summary"]
    assert NEW in report["summary"]          # the machine fact is still delivered


def test_free_form_keeps_words_when_artifact_is_missing(monkeypatch):
    monkeypatch.setenv("IRU_FREE_FORM_REPORT", "1")
    report = build_worker_report(task([read("step_1", OLD), answer()],
                                      ["summary.txt в папке IRU-EC01-LIVE"]))
    assert report["status"] == "partial"
    assert report["error_code"] == "artifact_receipt_missing"   # fact preserved
    assert MODEL_TEXT in report["summary"]
    assert "Обязательные файловые результаты не подтверждены" not in report["summary"]


def test_free_form_worker_words_do_not_replace_goal_conversation_fallback(monkeypatch):
    monkeypatch.setenv("IRU_FREE_FORM_REPORT", "1")
    goal = {"goal_id": "g1", "status": "partial", "terminal": True, "tasks": [
        {"required_for_goal": True, "conversational_response": MODEL_TEXT, "report": {}}]}
    text = orch.present_goal_result(goal)
    assert text == 'Часть поручения выполнить не удалось.'
    assert goal['tasks'][0]['conversational_response'] == MODEL_TEXT


def test_default_mode_keeps_the_existing_wording(monkeypatch):
    monkeypatch.delenv("IRU_FREE_FORM_REPORT", raising=False)
    report = build_worker_report(task([answer()], ["summary.txt в папке IRU-EC01-LIVE"]))
    assert "Обязательные файловые результаты не подтверждены" in report["summary"]
    goal = {"goal_id": "g1", "status": "partial", "terminal": True, "tasks": [
        {"required_for_goal": True, "conversational_response": MODEL_TEXT, "report": {}}]}
    assert orch.present_goal_result(goal).startswith("Часть поручения выполнить не удалось.")


def test_free_form_still_hides_unknown_outcome_behind_status(monkeypatch):
    """Free text never turns an unknown outcome into a claim of success."""
    monkeypatch.setenv("IRU_FREE_FORM_REPORT", "1")
    unknown = replace()
    unknown["result"] = {"status": "unknown", "error": "confirmed_transport_outcome_unknown",
                         "dispatched": True}
    report = build_worker_report(task([read("step_1", OLD), unknown, answer("Готово.")],
                                      ["project.txt в папке IRU-EC01-LIVE"]))
    assert report["status"] == "unknown"
    assert report["goal_completed"] is False

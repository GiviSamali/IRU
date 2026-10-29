"""EC-01C: artifact identity and evidence provenance.

Two defects from the 29 Oct LIVE are pinned here:

* a descriptive assignment label ("one.txt в папке ... с радиусом 0.21")
  must bind to its own verified receipt instead of reporting a missing one;
* a journal entry produced by another Worker must never become evidence of
  this Worker, and must never be re-attributed to it.
"""
import pytest

from server.worker_reports import build_worker_report


LIVE = [
    ("one.txt", "d2363d7ac2fce586a0b7ffe1c64c531a10e1ce4a30139f41aaf5eff2701d6a9d", 81,
     "one.txt в папке IRU-EC01-LIVE на рабочем столе givi с радиусом 0.21"),
    ("four.txt", "710ae661e709b08f88fb04ea930ba0f2249669aa52873017964df36cf0d22482", 110,
     "four.txt в папке IRU-EC01-LIVE на рабочем столе givi с заменёнными значениями"),
    ("mixed.txt", "8816ffb3f2b5daf1d358642b297a2f3225831db04a94c6e03d465e018111d296", 41,
     "mixed.txt в папке IRU-EC01-LIVE на рабочем столе givi с заменёнными значениями"),
    ("project.txt", "c662aeb0e6b88588258a7cb7fa2d8cc37d6bd7d3e017e706ed54b5b6efef8cc1", 67,
     "project.txt в папке IRU-EC01-LIVE на рабочем столе givi с заменённым названием"),
    ("summary.txt", "a33792a44a8cc5fe9a11243dfef94852012237e2ead19dffb8d4e01455dee761", 29,
     "summary.txt в папке IRU-EC01-LIVE на рабочем столе givi"),
]

ROOT = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE"


def write_step(task_id, name, digest, *, device="givi", verified=True, committed=True,
               step="step_1", status="success"):
    path = f"{ROOT}/{name}"
    return {"task_id": task_id, "step_id": step, "tool_name": "write_content", "status": status,
            "device_id": device, "target_device_id": device,
            "result": {"status": "ok" if status == "success" else status, "path": path,
                       "verified": verified, "committed": committed,
                       "file_sha256": digest, "total_size": 0}}


def answer_step(task_id, basis, *, step="step_2"):
    return {"task_id": task_id, "step_id": step, "tool_name": "answer.text", "status": "terminal",
            "result": {"answer_type": "grounded_report", "text": "Готово.", "basis": basis,
                       "self_check": {"depends_on_current_external_state": True,
                                      "claims_completed_action": True,
                                      "has_sufficient_evidence": True,
                                      "missing_evidence_question": ""}}}


def report(labels, commands, *, task_id="task-1", device="givi"):
    return build_worker_report({"task_id": task_id, "worker_id": "worker-1", "status": "done",
                                "device_ids": [device], "assigned_artifacts": list(labels),
                                "commands": commands, "answer": "Готово."})


def paths(rep):
    return {item["path"] for item in rep["artifacts"]}


@pytest.mark.parametrize("name,digest,size,label", LIVE)
def test_live_descriptive_label_binds_its_own_receipt(name, digest, size, label):
    """Regression for the four LIVE goals: file confirmed, report said missing."""
    rep = report([label], [write_step("task-1", name, digest), answer_step("task-1", ["step_1"])])
    assert f"{ROOT}/{name}" in paths(rep)
    assert rep["error_code"] != "artifact_receipt_missing"
    assert rep["error_code"] != "artifact_match_ambiguous"


@pytest.mark.parametrize("name,digest,size,label", LIVE)
def test_foreign_worker_entry_is_never_evidence(name, digest, size, label):
    """A receipt produced by another Task must not satisfy this assignment."""
    rep = report([label], [write_step("task-2", name, digest), answer_step("task-1", [])])
    assert f"{ROOT}/{name}" not in paths(rep)
    assert rep["error_code"] == "artifact_receipt_missing"
    assert rep["status"] != "success"


def test_two_workers_of_one_goal_do_not_share_evidence():
    """Mixed goal: each Worker sees only its own committed file."""
    project = write_step("task-project", "project.txt", LIVE[3][1])
    summary = write_step("task-summary", "summary.txt", LIVE[4][1], step="step_2")
    shared = [project, summary]

    project_report = report([LIVE[3][3]], shared, task_id="task-project")
    summary_report = report([LIVE[4][3]], shared, task_id="task-summary")

    assert paths(project_report) == {f"{ROOT}/project.txt"}
    assert paths(summary_report) == {f"{ROOT}/summary.txt"}
    assert project_report["error_code"] != "artifact_receipt_missing"
    assert summary_report["error_code"] != "artifact_receipt_missing"


def test_legacy_entry_without_provenance_stays_readable():
    """Old journals carry no producer; they must keep working."""
    legacy = write_step("task-1", "one.txt", LIVE[0][1])
    legacy.pop("task_id")
    rep = report([LIVE[0][3]], [legacy])
    assert f"{ROOT}/one.txt" in paths(rep)
    assert rep["error_code"] != "artifact_receipt_missing"


def test_same_name_on_another_device_is_not_bound():
    """Identical file names on different devices must not be mixed."""
    rep = report([LIVE[0][3]], [write_step("task-1", "one.txt", LIVE[0][1], device="other")],
                 device="givi")
    assert f"{ROOT}/one.txt" not in paths(rep)
    assert rep["status"] != "success"


def test_ambiguous_candidates_are_not_chosen_arbitrarily():
    """Two verified files for one descriptive requirement stays unmatched."""
    rep = report([LIVE[0][3]], [
        write_step("task-1", "one.txt", LIVE[0][1], step="step_1"),
        write_step("task-1", "four.txt", LIVE[1][1], step="step_2"),
    ])
    assert rep["error_code"] == "artifact_match_ambiguous"
    assert rep["status"] != "success"


def test_missing_file_stays_unconfirmed():
    rep = report([LIVE[0][3]], [answer_step("task-1", [])])
    assert rep["error_code"] == "artifact_receipt_missing"
    assert rep["goal_completed"] is False


def test_exact_assignment_name_still_binds():
    """Explicit file names keep resolving without the single-candidate rule."""
    rep = report(["one.txt"], [write_step("task-1", "one.txt", LIVE[0][1]),
                               write_step("task-1", "four.txt", LIVE[1][1], step="step_2")])
    assert f"{ROOT}/one.txt" in paths(rep)
    assert rep["error_code"] != "artifact_receipt_missing"


def test_unverified_write_grants_no_receipt():
    rep = report([LIVE[0][3]], [write_step("task-1", "one.txt", LIVE[0][1], verified=False)])
    assert f"{ROOT}/one.txt" not in paths(rep)
    assert rep["status"] != "success"


def test_unknown_outcome_never_becomes_success():
    unknown = write_step("task-1", "one.txt", LIVE[0][1], status="unknown")
    unknown["result"] = {"status": "unknown", "error": "confirmed_transport_outcome_unknown",
                        "dispatched": True}
    rep = report([LIVE[0][3]], [unknown, answer_step("task-1", ["step_1"])])
    assert rep["status"] in {"unknown", "partial"}
    assert rep["goal_completed"] is False


def test_owned_journal_drops_foreign_and_stamps_unowned():
    from server.worker_scheduler import _owned_journal

    own = {"step_id": "step_1", "task_id": "task-1"}
    foreign = {"step_id": "step_2", "task_id": "task-2"}
    unowned = {"step_id": "step_3"}
    kept = _owned_journal([own, foreign, unowned], "task-1")
    assert kept == [own, unowned]
    assert unowned["task_id"] == "task-1"
    assert foreign.get("task_id") == "task-2"

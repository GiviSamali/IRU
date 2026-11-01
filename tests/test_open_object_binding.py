"""An opened object is proven by the Assignment requirement and a real path.

The requirement lives in the accepted Assignment (`open_objects`); the proof is
the agent's own observation of the object's path, never the argument it was
given and never a window title.
"""
import pytest
from pathlib import PureWindowsPath

from server.worker_reports import build_worker_report

DOC = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE/Presentation.pptx"
SAME_NAME_OTHER_DIR = "C:/Users/russa/OneDrive/Desktop/Other/Presentation.pptx"
SIMILAR = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE/Presentation2.pptx"
OTHER = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE/Other.docx"


def open_step(required_path=DOC, *, observed_path=None, title="Presentation - PowerPoint",
              process="POWERPNT.EXE", class_name="PPTFrameClass", verified=True,
              status="opened_verified", step="step_1", requested="default", legacy=False,
              kind="file", hwnd=""):
    if legacy:
        return {"step_id": step, "tool_name": "app.launch", "status": "success",
                "target_device_id": "pc",
                "result": {"status": "launched_verified",
                           "window": {"title": title, "visible": True, "pid": 12}}}
    if requested == "default":
        requested = {"type": kind, "path": required_path}
    observed_path = observed_path if observed_path is not None else required_path
    observed = {"observed_path": observed_path, "kind": kind, "title": title,
                "process": process, "class_name": class_name, "hwnd": hwnd,
                "matched_name": PureWindowsPath(observed_path).name}
    return {"step_id": step, "tool_name": "app.launch", "status": "success",
            "target_device_id": "pc",
            "result": {"status": status, "requested_object": requested,
                       "object_open_verified": verified, "observed_object": observed,
                       "window": {"title": title, "visible": True, "pid": 12}}}


def goal(steps, objects, *, effects=None, request="Открой презентацию"):
    return {"task_id": "open", "status": "done", "device_ids": ["pc"],
             "window_effects": effects or ["open"],
            "open_objects": objects, "proposed_objective": request, "commands": steps}


def objects(*paths, device="pc", kind="file"):
    return [{"type": kind, "path": path, "device_id": device} for path in paths]


def completes(steps, required, **kwargs):
    terminal={'tool_name':'answer.text','status':'terminal','result':{
        '_result_contract':3,'text':'Requested objects opened','unresolved':[]}}
    return build_worker_report(goal([*steps,terminal], required, **kwargs))["goal_completed"]


def test_verified_document_open_completes():
    assert completes([open_step()], objects(DOC)) is True


def test_same_basename_from_another_directory_is_not_the_object():
    assert completes([open_step(observed_path=SAME_NAME_OTHER_DIR)], objects(DOC)) is False


def test_similar_names_are_not_the_object():
    assert completes([open_step(observed_path=SIMILAR)], objects(DOC)) is False


def test_two_foreign_documents_do_not_satisfy_two_required_objects():
    steps = [open_step(required_path=OTHER, observed_path=OTHER, title="Other - Word",
                       process="WINWORD.EXE", step="step_1"),
             open_step(required_path=SIMILAR, observed_path=SIMILAR, step="step_2")]
    assert completes(steps, objects(DOC, SAME_NAME_OTHER_DIR), effects=["open", "open"]) is False


def test_previously_open_window_with_another_document_is_rejected():
    # The window exists and the title matches, but the process was started for
    # a different file: the observed path is the authority, not the title.
    assert completes([open_step(title="Presentation - PowerPoint",
                                observed_path=SAME_NAME_OTHER_DIR)], objects(DOC)) is False


def test_helper_window_class_is_not_a_document():
    assert completes([open_step(class_name="ApplicationFrameWindow")], objects(DOC)) is False


def test_legacy_agent_without_object_receipt_cannot_prove_a_document():
    assert completes([open_step(legacy=True)], objects(DOC)) is False


def test_requirement_without_objects_cannot_be_proven_by_effect_name():
    assert completes([open_step()], []) is False


def test_device_mismatch_is_not_the_same_object():
    assert completes([open_step()], objects(DOC, device="other-device")) is False


def test_two_distinct_documents_complete():
    steps = [open_step(step="step_1"),
             open_step(required_path=OTHER, observed_path=OTHER, title="Other - Word",
                       process="WINWORD.EXE", step="step_2")]
    assert completes(steps, objects(DOC, OTHER), effects=["open", "open"]) is True


def test_same_document_twice_is_still_one_object():
    """Two demands for A and B are not satisfied by two opens of A."""
    steps = [open_step(step="step_1"), open_step(step="step_2")]
    assert completes(steps, objects(DOC, OTHER), effects=["open", "open"]) is False


def test_missing_application_reports_a_known_failure():
    step = open_step(verified=False, status="failed")
    step["result"]["error"] = "[WinError 193] %1 is not a valid Win32 application"
    report = build_worker_report(goal([step], objects(DOC)))
    assert report["goal_completed"] is False
    assert report["status"] in {"failed", "partial"}


# --- boundaries added after review -----------------------------------------


def test_document_in_command_line_but_not_loaded_is_not_proof():
    """The process was started with the path, but the app never loaded it."""
    step = open_step(verified=False, status="launched")
    step["result"].pop("observed_object")
    assert completes([step], objects(DOC)) is False


def test_path_as_substring_of_another_argument_is_not_the_object():
    assert completes([open_step(observed_path=DOC + ".bak")], objects(DOC)) is False


def test_document_closed_while_process_stays_is_not_proof():
    """Office process alive, document no longer in its collection."""
    step = open_step(verified=False, status="launched")
    step["result"]["observed_object"] = None
    assert completes([step], objects(DOC)) is False


def test_folder_needs_a_real_window_handle():
    folder = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE"
    step = open_step(required_path=folder, observed_path=folder, kind="folder",
                     title="IRU-EC01-LIVE", process="explorer.exe",
                     class_name="CabinetWClass", hwnd="")
    assert completes([step], [{"type": "folder", "path": folder, "device_id": "pc"}]) is False


def test_folder_with_its_own_explorer_window_is_proven():
    folder = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE"
    step = open_step(required_path=folder, observed_path=folder, kind="folder",
                     title="IRU-EC01-LIVE", process="explorer.exe",
                     class_name="CabinetWClass", hwnd="66052")
    assert completes([step], [{"type": "folder", "path": folder, "device_id": "pc"}]) is True


@pytest.mark.parametrize("dangerous", ["setup.lnk", "run.hta", "script.vbs", "x.js", "y.wsf"])
def test_dangerous_shell_association_is_never_a_document(dangerous):
    path = f"C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE/{dangerous}"
    step = open_step(required_path=path, observed_path=path, title=dangerous,
                     process="explorer.exe", class_name="CabinetWClass")
    assert completes([step], [{"type": "file", "path": path, "device_id": "pc"}]) is False


def test_declared_type_cannot_disguise_an_executable_object():
    path = "C:/Users/russa/OneDrive/Desktop/IRU-EC01-LIVE/run.hta"
    step = open_step(required_path=path, observed_path=path, kind="file", title="run.hta",
                     process="mshta.exe")
    assert completes([step], [{"type": "file", "path": path, "device_id": "pc"}]) is False

"""Decision contract tests; semantic LIVE quality requires user-run scenarios."""
import pytest
from pydantic import ValidationError

from server.orchestrator import Decision, SYSTEM
from server.worker_scheduler import validate_task_graph
from server.controller import _select_llm_route


def test_one_decision_carries_shared_research_and_three_deliverables():
    decision = Decision(intent="delegate", objective="News and documents", scope="device",
                        target_device_ids=["pc"], execution_mode="simple", assignments=[
                            {"key": "news", "objective": "Preserve today's news with dates and sources"},
                            *[{"key": key, "objective": f"Create {key} from preserved news", "depends_on": ["news"]}
                              for key in ("docx", "pptx", "xlsx")]])
    graph = validate_task_graph([item.model_dump() for item in decision.assignments])
    assert len(graph) == 4
    assert all(graph[key] == ["news"] for key in ("docx", "pptx", "xlsx"))


def test_assignments_are_strict_deliverables_not_executable_commands():
    with pytest.raises(ValidationError):
        Decision(intent="delegate", assignments=[{"key": "x", "objective": "x", "execute_cmd": "whoami"}])


@pytest.mark.parametrize("intent", ["conversation", "clarify", "cancel", "task_status"])
def test_non_delegation_needs_no_task_graph(intent):
    assert Decision(intent=intent).assignments == []


def test_routing_instructions_assign_decomposition_to_existing_orchestrator():
    assert "assignments" in SYSTEM
    assert "Отдельного режима PLAN и дополнительного Planner нет" in SYSTEM


def test_retired_pipeline_cannot_reach_controller():
    with pytest.raises(ValueError, match="execution_mode_unsupported"):
        _select_llm_route({"pipeline": True})
    assert _select_llm_route({}).name == "non_pipeline"


def test_pending_amendment_has_explicit_revision_and_no_new_task_graph():
    choice = Decision(intent="amend", task_id="pending", task_revision=3,
                      objective="Produce the same document with a shorter introduction")
    assert choice.task_revision == 3
    assert choice.assignments == []
    with pytest.raises(ValidationError):
        Decision(intent="amend", task_revision=0)


def test_goal_status_and_goal_extension_are_distinct_intents():
    status = Decision(intent="task_status", goal_id="owned-goal")
    extension = Decision(intent="delegate", goal_id="owned-goal", goal_revision=2,
        objective="Slides from the same research", assignments=[
            {"key":"slides", "objective":"Slides", "required_source_task_ids":["research"]}])
    assert status.assignments == []
    assert extension.assignments[0].depends_on == []
    assert extension.assignments[0].required_source_task_ids == ["research"]


def test_assigned_objective_limits_work_without_replacing_human_authority():
    from server.runtime_state import tasks
    from server.controller_shared import worker_assignment_scope
    tasks["assignment-boundary"] = {"goal_id":"goal", "original_request":"Research plus documents",
                                    "proposed_objective":"Create only slides"}
    try:
        objective, block = worker_assignment_scope("assignment-boundary")
        assert objective == "Create only slides"
        assert "permission ceiling" in block and "authority=data_only" in block
        assert "Do not execute sibling deliverables" in block
    finally:
        tasks.pop("assignment-boundary", None)


def test_no_stage_or_preparation_contract():
    from server.orchestrator import Assignment
    # Planning is over deliverables, not mandatory preparation stages.
    assert "РАЗВЕДКА" not in SYSTEM
    assert "reconnaissance" not in Assignment.model_fields
    decision = Decision(intent="delegate", objective="Two independent outputs",
        assignments=[
            {"key":"word","objective":"Write only the Word document","artifacts":["report.docx"]},
            {"key":"slides","objective":"Build only the slides","artifacts":["presentation.pptx"]},
        ])
    assert validate_task_graph([item.model_dump() for item in decision.assignments]) == {
        "word": [], "slides": []}
    with pytest.raises(ValidationError):
        Assignment(key="probe",objective="Prepare placeholders",stage="reconnaissance")


def test_assigned_worker_sees_only_own_artifact_contract():
    from server.runtime_state import tasks
    from server.controller_shared import worker_assignment_scope
    tasks["single-output"] = {
        "goal_id":"goal","proposed_objective":"Write the Word report",
        "assigned_artifacts":["report.docx"], "device_ids":["pc"]}
    try:
        objective, block = worker_assignment_scope("single-output")
        assert objective == "Write the Word report"
        assert "report.docx" in block
        assert "do not create, fill or modify it" in block
        assert "original human message" in block
    finally:
        tasks.pop("single-output",None)

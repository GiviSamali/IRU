"""Recovery uses the common Worker; no separate PLAN recovery state machine."""
import asyncio
import json

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.orchestrator import Assignment
from server.worker_scheduler import validate_task_graph


def call(name, arguments, index):
    return {"choices":[{"message":{"tool_calls":[{"id":str(index),"function":{
        "name":name,"arguments":json.dumps(arguments)}}]}}]}


def run_recovery(commands, observations):
    sent=[]
    responses=iter([*[call("execute_cmd",{"command":command},i) for i,command in enumerate(commands)],
        call("answer_report_failure",{"message":"Result is not confirmed","reason":"unverified",
            "recoverable":False,"suggested_next_action":"Inspect the remaining issue","basis":["step_1"]},"final")])
    results=iter(observations)
    async def completion(**kwargs):
        return next(responses)
    async def send(device,action,params):
        assert action=="execute_cmd"
        sent.append(params["command"])
        return next(results)
    outcome=asyncio.run(process_non_pipeline_command(user_message="Inspect and repair the script",
        device_id="pc",device_info={"os":"Windows"},send_command_fn=send,get_file_link_fn=lambda *a:"",
        chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=None,
        cfg={"model":"mock-model","answer_auditor_enabled":False},system_msg="system",
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,
        max_iterations=8,pick_model_fn=lambda *a:"mock-model",chat_completion_request_fn=completion))
    return outcome,sent


def test_distinct_failures_can_be_corrected_without_plan_recovery():
    commands=["python script.py","python repaired.py","python verify.py"]
    outcome,sent=run_recovery(commands,[
        {"returncode":1,"stderr":"UnicodeError: invalid encoding"},
        {"returncode":1,"stderr":"SyntaxError: invalid syntax"},
        {"returncode":0,"stdout":"observation obtained"}])
    assert sent==commands
    assert outcome["commands"][-1]["tool_name"]=="answer.report_failure"
    assert (outcome.get("task_receipt") or {}).get("goal_completed") is not True


def test_distinct_corrections_are_not_limited_by_retired_three_recovery_counter():
    commands=[f"python inspect_{i}.py" for i in range(4)]
    outcome,sent=run_recovery(commands,[{"returncode":1,"stderr":f"Synthetic failure {i}"} for i in range(4)])
    assert sent==commands
    assert outcome["commands"][-1]["tool_name"]=="answer.report_failure"
    assert all(entry.get("action")!="budget_guard" for entry in outcome["commands"])


def test_independent_deliverables_need_no_recovery_or_budget_flags():
    assignments=[Assignment(key=key,objective=f"Create {key}").model_dump() for key in ("Word","Excel","Report")]
    assert validate_task_graph(assignments)=={"Word":[],"Excel":[],"Report":[]}
    assert all("execution_budget" not in item and "independent_after_failure" not in item for item in assignments)

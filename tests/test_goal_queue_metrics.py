"""Read-only metrics must preserve missing usage rather than manufacture zeros."""
import json
import sqlite3

import pytest

from tools.goal_queue_metrics import measurements


@pytest.mark.parametrize("second,expected,complete", [
    ((None,None,None),(None,None,None),False),
    ((None,3,2),(None,9,6),False),
    ((0,0,0),(10,6,4),True),
    ((5,3,2),(15,9,6),True),
    ((-1,3,2),(None,9,6),False),
    (None,(None,None,None),False),
])
def test_export_preserves_incomplete_usage_and_does_not_write_database(tmp_path, second, expected, complete):
    path=tmp_path/"metrics-copy.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE llm_usage_events(user_id,poll_task_id,task_id,route,phase,total_tokens,prompt_tokens,completion_tokens,request_ok)")
        c.execute("CREATE TABLE worker_jobs(task_id,owner_user_id,state,payload,report,created_at,updated_at)")
        c.execute("CREATE TABLE orchestrator_turns(task_id,owner_user_id,response,created_at)")
        report={"task_id":"worker","status":"success","goal_completed":True,"artifacts":[]}
        c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?)",(
            "worker",7,"success",json.dumps({"worker_report":report}),None,1,2))
        if second is not None:
            for counts in ((10,6,4),second):
                c.execute("INSERT INTO llm_usage_events VALUES(?,?,?,?,?,?,?,?,?)",(
                    7,"worker","worker","worker","non_pipeline.iteration.1",*counts,1))
    before=path.read_bytes()
    result=measurements(path,7,["worker"],"fixture")
    assert tuple(result[name] for name in ("total_tokens","prompt_tokens","completion_tokens"))==expected
    assert result["token_counts_complete"] is complete
    assert result["recorded_llm_calls"]==(0 if second is None else 2)
    assert result["artifact_correctness"]=="requires_user_verification"
    assert path.read_bytes()==before

import asyncio
import json
import pytest
from server import database as db
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.worker_scheduler import WorkerScheduler, init_worker_storage, goal_view, owned_job
from server.worker_reports import build_worker_report
from test_goal_parallel_scheduler import complete
from server.controller_trust import enforce_trusted_answer, has_grounded_terminal_answer
from server.run_journal import append_answer_step, append_tool_step, validate_answer_text_payload
from server.tool_completion import synthesize_terminal_answer_payload
from test_controller_trust import _run_non_pipeline_case


def _execute_call(call_id, command, device_id=None):
    args={"command":command}
    if device_id:args["device_id"]=device_id
    return {"id":call_id,"function":{"name":"execute_cmd","arguments":json.dumps(args)}}


def evidence(journal, device, result, status="success"):
    append_tool_step(journal, {"action": "execute_cmd", "command": "check", "device_id": device,
        "target_device_id": device, "result": result, "status": status})


def test_grounded_recovered_answer_is_not_replaced_by_old_error():
    journal = []
    evidence(journal, "Second", {"error": "timeout"}, "failed")
    evidence(journal, "Second", {"returncode": 0, "stdout": "OK: steam_process_running"})
    payload = validate_answer_text_payload(synthesize_terminal_answer_payload(journal[-1]), journal)
    append_answer_step(journal, "answer_text", payload, target_device_id="Second")
    assert enforce_trusted_answer(payload["text"], journal) == payload["text"]
    evidence(journal, "Second", {"error": "new failure"}, "failed")
    assert "ошибкой" in enforce_trusted_answer(payload["text"], journal)


def test_unvalidated_success_does_not_hide_failure():
    journal = []
    evidence(journal, "Second", {"error": "not found"}, "failed")
    assert "not found" in enforce_trusted_answer("Готово", journal)


@pytest.mark.parametrize("tool,result", [
    ("app_open_url", {"status":"opened_verified","launched":True,"window_found":True,"url":"https://music.yandex.ru"}),
    ("app_launch", {"status":"launched_verified","window":{"process_name":"steam.exe"}}),
])
def test_verified_application_observation_does_not_complete_whole_goal(tool,result):
    journal=[]
    append_tool_step(journal,{"action":tool,"device_id":"pc","result":result,"status":"success"})
    report=build_worker_report({"task_id":"application-only","user_id":1,"device_ids":["1:pc"],
        "message":"Open an application and prepare a report","status":"done","commands":journal})
    assert report["goal_completed"] is False
    assert report["status"]!="success"


def test_verified_url_worker_stops_without_another_llm_or_verification(monkeypatch):
    monkeypatch.setattr(db, "get_device_profile", lambda *a, **kw: None)
    calls, sent = [], []
    async def completion(**kw):
        calls.append(kw)
        assert len(calls) <= 2, "No LLM calls after the terminal answer"
        if len(calls) == 1:
            return {"choices": [{"message": {"tool_calls": [{"id": "open", "function": {"name": "app_open_url", "arguments": json.dumps({"url": "https://music.yandex.ru"})}}]}}]}
        return {"choices": [{"message": {"tool_calls": [{"id": "answer", "function": {"name": "answer_text", "arguments": json.dumps({
            "answer_type": "grounded_report", "text": "Music opened.", "basis": ["step_1"],
            "self_check": {"depends_on_current_external_state": True, "claims_completed_action": True,
                           "has_sufficient_evidence": True, "missing_evidence_question": ""},
        })}}]}}]}
    async def send(device, action, params):
        sent.append((device, action))
        return {"status": "opened_verified", "launched": True, "window_found": True, "url": "https://music.yandex.ru"}
    result = asyncio.run(process_non_pipeline_command(user_message="Open music",device_id="device-1",
        device_info={"os":"Windows"},send_command_fn=send,get_file_link_fn=lambda *a:"",chat_history=[],
        user_id=None,chat_id=None,modes={},poll_task_id=None,cfg={"model":"mock","answer_auditor_enabled":False},
        system_msg="system",machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,
        max_iterations=4,pick_model_fn=lambda *a:"mock",chat_completion_request_fn=completion))
    assert len(calls) == 2 and sent == [("device-1", "app.open_url")]
    assert result["commands"][-1]["tool_name"] == "answer.text"


def test_non_pipeline_success_on_first_device_allows_explicit_second_action(monkeypatch):
    from server import database
    monkeypatch.setattr(database, "get_device_profile", lambda *a, **kw: None)
    sent = []
    async def send(device, action, params):
        sent.append(device)
        return {"returncode": 0, "stdout": "OK: opened_verified"}
    responses = [{"choices": [{"message": {"tool_calls": [_execute_call(str(i), "open", device)]}}]} for i, device in enumerate(["device-1", "device-2"])]
    responses.append({"choices": [{"message": {"content": ""}}]})
    _run_non_pipeline_case(responses, send_command_fn=send)
    assert sent == ["device-1", "device-2"]


@pytest.mark.parametrize("valid_terminal,fail_second", [(True,False),(True,True),(False,False),(False,True)])
def test_goal_preserves_each_device_result_without_summary_llm(tmp_path,monkeypatch,valid_terminal,fail_second):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"device-goal.sqlite")
    db.init_db();init_worker_storage()
    owner=db.create_user("device-goal")
    chat=db.create_chat(owner["id"],"device outcomes")["id"]
    started=[]
    async def execute(task):
        target=task["device_ids"][0]
        started.append(target)
        journal=[]
        if target.endswith(":b") and fail_second:
            evidence(journal,"b",{"error":"not found"},"failed")
            task.update(status="error",answer="Device B failed",commands=journal)
        elif valid_terminal:
            # Synthetic grounded source report: no actual device command is run.
            complete(task)
            task["commands"][0].update(tool_name="execute_cmd", device_id=target,
                command="Get-Content C:/Temp/note.txt",
                result={"returncode":0,"stdout":f"Observed state on {target}","stderr":""})
        else:
            evidence(journal,target,{"returncode":0,"stdout":"OK: opened_verified"})
            task.update(status="done",answer="Opened",commands=journal)
    async def scenario():
        scheduler=WorkerScheduler(execute)
        try:
            devices=[f"{owner['id']}:{name}" for name in ("a","b")]
            admitted=await scheduler.submit_goal({"user_id":owner["id"],"chat_id":chat,
                "message":"Read both devices","device_ids":devices},[
                    {"key":name,"objective":f"Read device {name}","target_device_ids":[device]}
                    for name,device in zip(("a","b"),devices)],request_key="both-devices")
            await scheduler.runners[owner["id"]]
            view=goal_view(admitted["goal_id"],owner["id"],chat)
            assert started==devices and len(view["tasks"])==2
            assert view["goal_completed"] is (valid_terminal and not fail_second)
            assert [item["objective"] for item in view["tasks"]]==["Read device a","Read device b"]
            if fail_second:
                assert view["tasks"][1]["report"]["goal_completed"] is False
                assert view["tasks"][1]["result"] is None
                saved=owned_job(view["tasks"][1]["task_id"],owner["id"])
                assert json.loads(saved["payload"])["commands"][0]["result"]["error"]=="not found"
            if not valid_terminal:
                assert all(item["result"] is None for item in view["tasks"])
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_other_device_success_cannot_hide_failed_device():
    journal = []
    evidence(journal, "a", {"error": "not opened"}, "failed")
    evidence(journal, "b", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    append_answer_step(journal, "answer_text", payload, target_device_id="b")
    assert "not opened" in enforce_trusted_answer(payload["text"], journal)


def test_broadcast_recovered_grounded_result_is_success():
    from server.task_runtime import _device_execution_status
    journal = []
    evidence(journal, "a", {"error": "timeout"}, "failed")
    evidence(journal, "a", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    append_answer_step(journal, "answer_text", payload, target_device_id="a")
    assert _device_execution_status({"answer": payload["text"], "commands": journal}) == "ok"


def test_fallback_report_keeps_confirmed_results_from_both_devices():
    from server.tool_completion import synthesize_device_terminal_report
    journal = []
    evidence(journal, "givi", {"returncode": 0, "stdout": "OK: music_opened"})
    evidence(journal, "Second", {"returncode": 0, "stdout": "OK: steam_opened"})
    payload = validate_answer_text_payload(synthesize_device_terminal_report(journal, journal[-1]), journal)
    assert "givi: OK: music_opened" in payload["text"]
    assert "Second: OK: steam_opened" in payload["text"]
    assert len(payload["basis"]) == 2


def test_runtime_keeps_validated_markdown_recovery_answer(monkeypatch):
    from server import task_runtime as rt
    journal = []
    evidence(journal, "a", {"error": "timeout"}, "failed")
    evidence(journal, "a", {"returncode": 0, "stdout": "OK: opened_verified"})
    payload = synthesize_terminal_answer_payload(journal[-1])
    payload["text"] = "**Приложение открыто.**"
    append_answer_step(journal, "answer_text", payload, target_device_id="a")
    rt.devices["1:a"] = {"user_id": 1, "info": {"os": "Linux"}, "ws": object()}
    rt.tasks["report"] = {"results": {}, "status": "running", "modes": {}}
    monkeypatch.setattr(rt, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    async def process(**kw): return {"answer": payload["text"], "commands": journal}
    monkeypatch.setattr(rt, "process_nl_command", process)
    asyncio.run(rt.run_nl_task("report", 1, "open", ["1:a"], 1))
    assert rt.tasks["report"]["answer"] == payload["text"]
    assert has_grounded_terminal_answer(rt.tasks["report"]["answer"], journal)


def test_broadcast_runtime_keeps_each_recovered_answer(monkeypatch):
    from server import task_runtime as rt
    for device in ("a", "b"):
        rt.devices["1:" + device] = {"user_id": 1, "info": {"os": "Linux", "hostname": device}, "ws": object()}
    rt.tasks["broadcast-report"] = {"results": {}, "status": "running", "modes": {}}
    monkeypatch.setattr(rt, "get_device_profile", lambda *a, **kw: None)
    monkeypatch.setattr(rt, "add_message", lambda *a, **kw: None)
    async def process(**kw):
        device = kw["device_id"]
        journal = []
        evidence(journal, device, {"error": "old timeout"}, "failed")
        evidence(journal, device, {"returncode": 0, "stdout": "OK: opened_verified"})
        payload = synthesize_terminal_answer_payload(journal[-1])
        append_answer_step(journal, "answer_text", payload, target_device_id=device)
        return {"answer": payload["text"], "commands": journal}
    monkeypatch.setattr(rt, "process_nl_command", process)
    asyncio.run(rt.run_nl_task("broadcast-report", 1, "open", ["1:a", "1:b"], 1))
    task = rt.tasks["broadcast-report"]
    assert task["status"] == "done" and task["overall_status"] == "success"
    assert "[a] ok" in task["answer"] and "[b] ok" in task["answer"]
    assert "old timeout" not in task["answer"]


def test_device_fallback_preserves_failure_without_claiming_whole_goal():
    from server.tool_completion import synthesize_device_terminal_report
    journal = []
    evidence(journal, "a", {"returncode": 0, "stdout": "OK: observed_a"})
    evidence(journal, "b", {"error": "device disconnected"}, "failed")
    payload = validate_answer_text_payload(synthesize_device_terminal_report(journal, journal[-1]), journal)
    assert "a: OK: observed_a" in payload["text"]
    assert "b: device disconnected" in payload["text"]
    assert len(payload["basis"]) == 2
    assert payload["answer_type"] == "partial_report"
    assert payload["self_check"]["claims_completed_action"] is False


def test_device_observations_are_preserved_without_action_completion():
    from server.tool_completion import synthesize_device_terminal_report
    journal = []
    evidence(journal, "a", {"returncode": 0, "stdout": "existing.txt"})
    evidence(journal, "b", {"returncode": 0, "stdout": "process started"})
    payload = validate_answer_text_payload(synthesize_device_terminal_report(journal, journal[-1]), journal)
    assert "a: existing.txt" in payload["text"] and "b: process started" in payload["text"]
    assert len(payload["basis"]) == 2
    assert payload["answer_type"] == "partial_report"
    assert payload["self_check"]["claims_completed_action"] is False

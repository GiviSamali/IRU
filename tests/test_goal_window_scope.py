"""Window scope comes from semantic intake; native effects are verified separately."""
import asyncio
import json

import pytest

from server import database as db
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.runtime_state import tasks
from server.worker_scheduler import validated_execution_scope
from server.window_policy import ordinary_window_request, direct_window_action, window_action_sequence


@pytest.mark.parametrize("user_message", ["Убери это окно с экрана, оставив приложение работать", "Minimize the active window", "Сверни"])
def test_semantic_window_scope_has_no_keyword_shortcut(monkeypatch, user_message):
    monkeypatch.setattr(db, "get_device_profile", lambda *a, **kw: None)
    tasks["semantic-window"] = {"task_id":"semantic-window", "goal_id":"goal", "execution_scope":"window",
        "window_effects":["minimize"], "proposed_objective":"Minimize the active window"}
    llm, dispatched = [], []
    async def completion(**kwargs):
        llm.append(kwargs)
        return {"choices":[{"message":{"tool_calls":[{"id":"window", "function":{
            "name":"window_control", "arguments":json.dumps({"action":"minimize","target":"current"})}}]}}]}
    async def send(device, action, arguments):
        dispatched.append((device, action, arguments))
        return {"status":"success", "completion_state":"success", "action":"minimize", "summary":"Window minimized",
                "response_policy":"silent_on_success", "window":{"window_id":"a"*32,"pid":123}}
    try:
        result = asyncio.run(process_non_pipeline_command(user_message=user_message,device_id="pc",device_info={"os":"Windows"},
            send_command_fn=send,get_file_link_fn=lambda *a:"",chat_history=[],user_id=None,chat_id=None,modes={},
            poll_task_id="semantic-window",cfg={"model":"mock-model"},system_msg="system",machine_guid=None,mem_user_id=None,
            non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=4,pick_model_fn=lambda *a:"mock-model",
            chat_completion_request_fn=completion))
        assert len(llm) == 1  # A real Worker decision still chooses the native tool.
        assert dispatched == [("pc","window.control",{"action":"minimize","target":"current"})]
        assert result["commands"][-1]["status"] == "terminal"
        assert not ordinary_window_request(user_message)
        assert direct_window_action(user_message, [], "pc") is None
        assert window_action_sequence(user_message) == []
    finally:
        tasks.pop("semantic-window", None)


@pytest.mark.parametrize("scope,effects", [("window",["move_virtual_desktop"]),("general",["close"]),("shell",[])])
def test_scope_contract_rejects_unsupported_effects_or_scope(scope, effects):
    with pytest.raises(ValueError, match="invalid_worker_execution_scope"):
        validated_execution_scope(scope, effects)

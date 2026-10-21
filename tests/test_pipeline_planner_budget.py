"""There is one semantic Orchestrator call, with no separate Planner budget."""
import asyncio
import json

import pytest

from server import orchestrator as orch
from server.controller_tools import TOOLS, NON_PIPELINE_TOOLS, WORKER_TOOLS


@pytest.mark.parametrize("message", [
    "Create three descriptions", "Three separate texts would help; prepare them", "Сделай три отдельных описания",
])
def test_deliverable_graph_uses_one_existing_orchestrator_call(monkeypatch, message):
    calls = []
    payload = {"intent":"delegate", "scope":"server", "objective":"Three descriptions", "execution_mode":"simple",
               "assignments":[{"key":key,"objective":f"Description {key}"} for key in ("a","b","c")]}
    monkeypatch.setattr(orch, "load_llm_config", lambda: {"model":"mock-model"})
    async def completion(*args, **kwargs):
        calls.append((args,kwargs))
        return {"choices":[{"finish_reason":"tool_calls", "message":{"tool_calls":[{
            "id":"decision", "type":"function", "function":{"name":"orchestrator_decision", "arguments":json.dumps(payload)}}]}}]}
    monkeypatch.setattr(orch, "_chat_completion_request", completion)
    choice, metrics = asyncio.run(orch.decide(message, {"tasks":[],"devices":[]}, user_id=1,chat_id=2,task_id="turn"))
    assert len(calls) == 1 and metrics["llm_calls"] == 1
    assert len(choice.assignments) == 3
    assert calls[0][0][3][-1] == {"role":"user","content":message}
    assert calls[0][1]["phase"] == "orchestrator"
    assert calls[0][1]["tools"][0]["function"]["name"] == "orchestrator_decision"


@pytest.mark.parametrize("finish,arguments", [("length",'{}'),("tool_calls",'{invalid')])
def test_invalid_or_truncated_intake_is_not_retried_by_a_planner(monkeypatch, finish, arguments):
    calls = []
    monkeypatch.setattr(orch, "load_llm_config", lambda: {"model":"mock-model"})
    async def completion(*args, **kwargs):
        calls.append(1)
        return {"choices":[{"finish_reason":finish,"message":{"tool_calls":[{
            "function":{"name":"orchestrator_decision","arguments":arguments}}]}}]}
    monkeypatch.setattr(orch, "_chat_completion_request", completion)
    with pytest.raises(ValueError):
        asyncio.run(orch.decide("request",{},user_id=1,chat_id=2,task_id="turn"))
    assert calls == [1]


def test_worker_tools_have_no_plan_tracking_or_separate_plan_worker_set():
    assert WORKER_TOOLS is NON_PIPELINE_TOOLS
    assert not {"create_plan","mark_step"}.intersection(tool["function"]["name"] for tool in TOOLS)
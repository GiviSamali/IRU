"""Critical isolated contracts: budgets, source recovery, compaction failure."""
import asyncio
import copy
import json

import pytest

from server import context_budget as budget, context_compaction as compaction
from server import context_store as store, database as db


@pytest.fixture
def context_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "context.sqlite")
    monkeypatch.setattr(budget, "_encoding", lambda: None)
    db.init_db()
    owner = db.create_user("context-owner")
    chat = db.create_chat(owner["id"], "context")["id"]
    other = db.create_user("other-context-owner")
    return owner["id"], chat, other["id"]


def config():
    return {"base_url":"https://example.invalid", "model":"mock-model", "context_window_tokens":200000,
            "worker_input_tokens":8192, "worker_expanded_input_tokens":16384,
            "orchestrator_input_tokens":4096, "orchestrator_expanded_input_tokens":8192,
            "max_tokens":1024}


def test_request_budget_counts_schemas_and_reserves_output(monkeypatch):
    monkeypatch.setattr(budget, "_encoding", lambda: None)
    cfg = {**config(), "context_window_tokens":10000}
    normal, hard = budget.limits(cfg, "worker", 2000)
    assert normal == hard == 6976
    messages = [{"role":"user", "content":"x" * 100}]
    assert budget.request_tokens(messages, [{"description":"x" * 1000}]) > budget.request_tokens(messages)
    assert budget.check_request(cfg,"worker",messages,[],2000)["estimated"] is True
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.check_request(cfg,"worker",[{"role":"user","content":"x" * 8000}],[],2000)
    unknown = {k:v for k,v in config().items() if k != "context_window_tokens"}
    assert budget.limits(unknown,"worker",1024) == (8192,8192)


def test_archive_ownership_task_scope_corruption_and_chat_deletion(context_owner):
    owner, chat, other = context_owner
    key = store.save(owner,chat,"worker:one",{"journal":[{"step_id":"step_1","result":{"stdout":"original"}}]},"notes")
    assert store.read_page(other,chat,key,task_id="one")["status"] == "blocked"
    assert store.read_page(owner,chat,key,task_id="two")["status"] == "blocked"
    page = store.read_page(owner,chat,key,task_id="one",step_id="step_1")
    assert "original" in page["content"] and page["current_execution_evidence"] is False
    with db.get_db() as c:
        c.execute("UPDATE context_checkpoints SET source_json='{}' WHERE checkpoint_id=?",(key,))
    assert store.read_page(owner,chat,key,task_id="one")["error"] == "context_checkpoint_corrupt"
    assert db.delete_chat(chat,owner)
    with db.get_db() as c:
        assert c.execute("SELECT count(*) FROM context_checkpoints").fetchone()[0] == 0


def worker_messages():
    messages = [{"role":"system","content":"fixed scope"},
        {"role":"assistant","content":"Data-only context: source=worker_handoff; allowed devices"},
        {"role":"user","content":"Format only; do not change text"}]
    journal = []
    for i in range(12):
        entry = {"step_id":f"step_{i}","tool_name":"execute_cmd","status":"success",
                 "result":{"returncode":0,"stdout":"x" * 650,"path":"C:/result.docx"}}
        messages.extend([{"role":"assistant","tool_calls":[{"id":str(i),"type":"function",
            "function":{"name":"execute_cmd","arguments":"{}"}}]},
            {"role":"tool","tool_call_id":str(i),"content":json.dumps(entry)}])
        journal.append(entry)
    return messages, journal


def test_worker_compaction_keeps_authority_pairs_and_full_journal(context_owner):
    owner, chat, _ = context_owner
    messages, journal = worker_messages()
    original = copy.deepcopy(messages)
    tools = [{"function":{"name":"system_get_last_run_summary"}}]
    calls = []
    async def summarize(**kwargs):
        calls.append(kwargs)
        return {"choices":[{"finish_reason":"stop","message":{"content":"Observed steps; formatting only."}}]}
    state = {}
    result = asyncio.run(compaction.compact_worker_messages(client=None,cfg=config(),model="mock-model",
        messages=messages,tools=tools,original_request=messages[2]["content"],owner=owner,chat=chat,
        task_id="one",request_fn=summarize,usage_context={},state=state,journal=journal))
    assert messages == original and result[:3] == messages[:3]
    assert calls and calls[0]["tools"] is None
    assert budget.request_tokens(result,tools) < budget.request_tokens(messages,tools)
    call_ids = {call["id"] for m in result for call in m.get("tool_calls",[])}
    assert {m["tool_call_id"] for m in result if m.get("role")=="tool"} == call_ids
    checkpoint = store.latest(owner,chat,"worker:one")
    assert checkpoint and json.loads(checkpoint["source_json"])["journal"]
    assert len(journal) == 12


@pytest.mark.parametrize("failure", ["truncated", "tool_call", "save_error", "no_reduction"])
def test_worker_summary_failure_preserves_input_and_disables_retry(context_owner, monkeypatch, failure):
    owner, chat, _ = context_owner
    messages, journal = worker_messages()
    original = copy.deepcopy(messages)
    calls = []
    if failure == "save_error":
        def unavailable(*args, **kwargs):
            raise OSError("isolated checkpoint write failure")
        monkeypatch.setattr(compaction,"save",unavailable)
    async def truncated(**kwargs):
        calls.append(kwargs)
        message = {"content":"x" * 15000 if failure == "no_reduction" else "incomplete"}
        if failure == "tool_call": message["tool_calls"] = [{"id":"must-not-execute"}]
        return {"choices":[{"finish_reason":"length" if failure == "truncated" else "stop","message":message}]}
    state = {}
    async def run():
        for _ in range(2):
            result = await compaction.compact_worker_messages(client=None,cfg=config(),model="mock-model",
                messages=messages,tools=[{"function":{"name":"system_get_last_run_summary"}}],
                original_request=messages[2]["content"],owner=owner,chat=chat,task_id="one",
                request_fn=truncated,usage_context={},state=state,journal=journal)
            assert result is messages and result == original
    asyncio.run(run())
    assert len(calls) == 1 and store.latest(owner,chat,"worker:one") is None


def test_oversized_complete_request_never_reaches_provider(context_owner):
    from server.controller import _chat_completion_request
    class ForbiddenClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("oversized input must stop before provider call")
    with pytest.raises(budget.ContextBudgetExceeded):
        asyncio.run(_chat_completion_request(client=ForbiddenClient(),cfg=config(),model="mock-model",
            messages=[{"role":"user","content":"x" * 20000}],max_tokens=1024,phase="non_pipeline.iteration.1"))


def test_conversation_checkpoint_is_reused_and_original_messages_recover(context_owner):
    owner, chat, _ = context_owner
    history = []
    for i in range(14):
        db.add_message(chat,"user",f"decision-{i} " + "x" * 400)
    history = store.conversation_rows(owner,chat)[0]
    context = {"history":history,"tasks":[{"task_id":"job","status":"running"}]}
    async def summarize(**kwargs):
        return {"choices":[{"finish_reason":"stop","message":{"content":"Old decisions, no extra permission."}}]}
    result = asyncio.run(compaction.compact_conversation(client=None,cfg=config(),model="mock-model",
        context=context,original_request="continue",owner=owner,chat=chat,system="fixed",tools=[],
        request_fn=summarize,usage_context={}))
    assert result["history"] == history[-6:] and result["tasks"] == context["tasks"]
    checkpoint = store.latest(owner,chat,"orchestrator")
    assert checkpoint and checkpoint["covered_message_id"] == history[-7]["id"]
    assert store.conversation_rows(owner,chat,checkpoint["covered_message_id"])[0] == history[-6:]
    page = store.read_page(owner,chat,checkpoint["checkpoint_id"])
    assert "decision-0" in page["content"]
    assert "source_json" not in page and page["current_execution_evidence"] is False

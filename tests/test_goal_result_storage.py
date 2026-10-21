"""User-run tests for the additive queue result storage contract."""
import hashlib
import json
import asyncio
import time

import pytest

from server import database as db
from server.worker_scheduler import init_worker_storage, _freeze_result, pinned_results, validate_task_graph
from server.worker_scheduler import WorkerScheduler, owned_job
from server.worker_scheduler import goal_view


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "goals.sqlite")
    db.init_db()
    init_worker_storage()
    owner = db.create_user("goal-result-owner")
    chat = db.create_chat(owner["id"], "results")["id"]
    with db.get_db() as c:
        for tid in ("source", "consumer"):
            c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (tid, owner["id"], chat, "queued", "{}", None, None, 1, 1, None))
    return owner["id"], chat


def freeze(storage, status="success"):
    owner, chat = storage
    task = {"task_id": "source", "user_id": owner, "chat_id": chat,
            "device_ids": [f"{owner}:pc"], "answer": "fact\n" * 6000,
            "commands": [{"result": {"url": "https://example.org", "observed_at": "2026-10-20"}}]}
    report = {"task_id": "source", "status": status,
              "goal_completed": status == "success", "evidence_refs": ["observation-1"]}
    with db.get_db() as c:
        _freeze_result(c, task, report)
    return task, report


def test_full_result_is_immutable_and_scope_checked(storage):
    task, report = freeze(storage)
    owner, chat = storage
    with db.get_db() as c:
        row = c.execute("SELECT * FROM worker_results").fetchone()
        assert json.loads(row["content"])["answer"] == task["answer"]
        assert row["sha256"] == hashlib.sha256(row["content"].encode()).hexdigest()
        c.execute("INSERT INTO worker_dependencies VALUES(?,?,?)",
                  ("consumer", "source", row["result_id"]))
        _freeze_result(c, task, report)  # Idempotent projection.
    results = pinned_results("consumer", owner, chat, task["device_ids"])
    assert results[0]["data"]["commands"] == task["commands"]
    with pytest.raises(ValueError, match="device_not_allowed"):
        pinned_results("consumer", owner, chat, [])
    with pytest.raises(ValueError, match="consumer_not_owned"):
        pinned_results("consumer", owner + 1, chat, task["device_ids"])
    with db.get_db() as c:
        with pytest.raises(ValueError, match="immutable_result_conflict"):
            _freeze_result(c, {**task, "answer": "replacement"}, report)


@pytest.mark.parametrize("status", ["partial", "unknown", "failed", "cancelled"])
def test_unconfirmed_outcome_cannot_publish_shared_result(storage, status):
    freeze(storage, status)
    with db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_results").fetchone()[0] == 0


def test_unresolved_dependency_cannot_disappear_through_result_join(storage):
    owner, chat = storage
    with db.get_db() as c:
        c.execute("INSERT INTO worker_dependencies VALUES(?,?,NULL)", ("consumer", "source"))
    with pytest.raises(ValueError, match="dependency_result_missing"):
        pinned_results("consumer", owner, chat, [])


def test_goal_consumer_cannot_expand_its_persisted_device_scope(storage):
    owner, chat = storage
    with db.get_db() as c:
        c.execute("UPDATE worker_jobs SET payload=? WHERE task_id='consumer'", (
            json.dumps({"goal_id":"goal", "device_ids":[f"{owner}:pc"]}),))
    with pytest.raises(ValueError, match="dependency_consumer_device_mismatch"):
        pinned_results("consumer", owner, chat, [f"{owner}:pc", f"{owner}:other"])


def test_additive_migration_preserves_legacy_payload(storage):
    init_worker_storage()
    with db.get_db() as c:
        assert c.execute("SELECT payload FROM worker_jobs WHERE task_id='source'").fetchone()[0] == "{}"


def test_valid_hash_cannot_substitute_a_different_task_result(storage):
    task, report = freeze(storage)
    owner, chat = storage
    with db.get_db() as c:
        _freeze_result(c, {**task, "task_id":"consumer"}, {**report, "task_id":"consumer"})
        c.execute("INSERT INTO worker_dependencies VALUES(?,?,?)", ("consumer", "source", "result:consumer"))
    with pytest.raises(ValueError, match="dependency_source_mismatch"):
        pinned_results("consumer", owner, chat, task["device_ids"])


def test_shared_research_graph_has_three_sibling_consumers():
    assignments = [{"key": "research", "objective": "Collect and preserve research"}]
    assignments += [{"key": key, "objective": f"Create {key}", "depends_on": ["research"]}
                    for key in ("word", "slides", "table")]
    graph = validate_task_graph(assignments)
    assert graph == {"research": [], "word": ["research"], "slides": ["research"], "table": ["research"]}


@pytest.mark.parametrize("edges, error", [
    ({"a": ["b"], "b": ["a"]}, "dependency_cycle"),
    ({"a": ["a"]}, "dependency_cycle"),
    ({"a": ["missing"]}, "dependency_task_missing"),
])
def test_invalid_graph_is_rejected_before_admission(edges, error):
    with pytest.raises(ValueError, match=error):
        validate_task_graph([{"key": key, "objective": key, "depends_on": deps}
                             for key, deps in edges.items()])


def test_batch_admission_is_idempotent_and_waiting_does_not_execute(storage):
    owner, chat = storage
    with db.get_db() as c:
        c.execute("DELETE FROM worker_jobs WHERE task_id IN ('source','consumer')")

    async def scenario():
        started = []
        source_started = asyncio.Event()
        gate = asyncio.Event()

        async def execute(task):
            started.append(task["proposed_objective"])
            source_started.set()
            await gate.wait()
            task.update(status="failed", commands=[], answer="source failed")

        scheduler = WorkerScheduler(execute)
        template = {"user_id": owner, "chat_id": chat, "device_ids": [],
                    "message": "research and document", "kind": "worker", "created_at": time.time()}
        assignments = [{"key": "research", "objective": "research"},
                       {"key": "document", "objective": "document", "depends_on": ["research"]}]
        result = await scheduler.submit_goal(template, assignments, request_key="batch")
        duplicate = await scheduler.submit_goal(template, assignments, request_key="batch")
        assert result["goal_id"] == duplicate["goal_id"]
        assert [t["task_id"] for t in result["tasks"]] == [t["task_id"] for t in duplicate["tasks"]]
        await asyncio.wait_for(source_started.wait(), timeout=5)
        assert started == ["research"]
        consumer = result["tasks"][1]
        assert owned_job(consumer["task_id"], owner)["state"] == "waiting_dependency"
        gate.set()
        await scheduler.runners[owner]
        assert started == ["research"]
        assert owned_job(consumer["task_id"], owner)["state"] == "waiting_dependency"
        extension = [{"key": "slides", "objective": "slides",
                      "required_source_task_ids": [result["tasks"][0]["task_id"]]}]
        added = await scheduler.submit_goal({**template, "message": "Also slides"}, extension,
            request_key="extension", goal_id=result["goal_id"], expected_revision=1)
        assert added["goal_id"] == result["goal_id"]
        assert len(added["tasks"]) == 1
        retried = await scheduler.submit_goal({**template, "message": "Also slides"}, extension,
            request_key="extension", goal_id=result["goal_id"], expected_revision=1)
        assert [t["task_id"] for t in retried["tasks"]] == [added["tasks"][0]["task_id"]]
        assert goal_view(result["goal_id"], owner, chat)["revision"] == 2
        original_retry = await scheduler.submit_goal(template, assignments, request_key="batch")
        assert [t["task_id"] for t in original_retry["tasks"]] == [t["task_id"] for t in result["tasks"]]
        assert added["tasks"][0]["task_id"] not in {t["task_id"] for t in original_retry["tasks"]}
        with pytest.raises(ValueError, match="request_id_conflict"):
            await scheduler.submit_goal({**template, "message":"A changed request"}, assignments, request_key="batch")
        assert goal_view(result["goal_id"], owner, chat)["revision"] == 2
        with pytest.raises(ValueError, match="goal_revision_conflict"):
            await scheduler.submit_goal(template, extension, request_key="stale-extension",
                goal_id=result["goal_id"], expected_revision=1)
        await scheduler.cancel(consumer["task_id"], owner)
        assert owned_job(consumer["task_id"], owner)["state"] == "cancelled"
        await scheduler.shutdown()

    asyncio.run(scenario())


def prepare_goal(storage):
    owner, chat = storage
    with db.get_db() as c:
        c.execute("INSERT INTO worker_goals VALUES(?,?,?,?,?,?)", ("goal", owner, chat, "original", 1, 1))
        for tid in ("source", "consumer"):
            payload = {"task_id": tid, "user_id": owner, "chat_id": chat,
                       "message": tid, "original_request": "original", "device_ids": [],
                       "goal_id": "goal", "status": "queued"}
            c.execute("UPDATE worker_jobs SET payload=? WHERE task_id=?", (json.dumps(payload), tid))
            c.execute("INSERT INTO worker_goal_tasks VALUES(?,?,?)", (tid, "goal", 1))


@pytest.mark.parametrize("mismatch", ["goal", "chat", "owner", "device"])
def test_goal_extension_rejects_foreign_dependency_without_partial_admission(storage, mismatch):
    prepare_goal(storage)
    owner, chat = storage
    source_owner, source_chat, source_goal = owner, chat, "other-goal"
    if mismatch == "owner":
        source_owner = db.create_user("other-result-owner")["id"]
    if mismatch in {"owner", "chat"}:
        source_chat = db.create_chat(source_owner, "other result chat")["id"]
    source_devices = [f"{owner}:other-device"] if mismatch == "device" else []
    if mismatch == "device":
        source_goal = "goal"
    with db.get_db() as c:
        if source_goal != "goal":
            c.execute("INSERT INTO worker_goals VALUES(?,?,?,?,?,?)", (
                source_goal, source_owner, source_chat, "Other goal", 1, 1))
        c.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "foreign-source", source_owner, source_chat, "queued", json.dumps({
                "task_id":"foreign-source", "goal_id":source_goal, "user_id":source_owner,
                "chat_id":source_chat, "device_ids":source_devices}), None, None, 1, 1, None))
        c.execute("INSERT INTO worker_goal_tasks VALUES(?,?,?)", ("foreign-source", source_goal, 1))

    async def scenario():
        scheduler = WorkerScheduler()
        expected = "dependency_device_not_allowed" if mismatch == "device" else "dependency_scope_mismatch"
        with pytest.raises(ValueError, match=expected):
            await scheduler.submit_goal({"user_id":owner, "chat_id":chat,
                "message":"Create slides from this goal's source", "device_ids":[]}, [
                    {"key":"slides", "objective":"Slides", "required_source_task_ids":["foreign-source"]}],
                request_key="invalid-extension", goal_id="goal", expected_revision=1)
        with db.get_db() as c:
            assert c.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0] == 3
            assert c.execute("SELECT revision FROM worker_goals WHERE goal_id='goal'").fetchone()[0] == 1
            assert c.execute("SELECT COUNT(*) FROM worker_goal_requests").fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM worker_goal_request_tasks").fetchone()[0] == 0
        assert not scheduler.runners
    asyncio.run(scenario())


def test_pending_amendment_preserves_authority_and_requires_current_revision(storage):
    prepare_goal(storage)
    owner, chat = storage
    scheduler = WorkerScheduler()
    result = scheduler.amend_pending("consumer", owner, chat, expected_revision=1,
                                    human_request="Use a shorter document", objective="Short document")
    assert result["revision"] == 2
    payload = json.loads(owned_job("consumer", owner)["payload"])
    assert payload["original_request"].startswith("original\n")
    assert "Use a shorter document" in payload["original_request"]
    assert payload["proposed_objective"] == "Short document"
    with pytest.raises(ValueError, match="revision_conflict"):
        scheduler.amend_pending("consumer", owner, chat, expected_revision=1,
                                human_request="stale", objective="stale")
    with db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_task_revisions").fetchone()[0] == 1
        c.execute("UPDATE worker_jobs SET state='running' WHERE task_id='consumer'")
    with pytest.raises(ValueError, match="already_dispatched"):
        scheduler.amend_pending("consumer", owner, chat, expected_revision=2,
                                human_request="late", objective="late")


def test_goal_success_requires_all_members_and_owned_chat(storage):
    prepare_goal(storage)
    owner, chat = storage
    assert goal_view("goal", owner, chat)["goal_completed"] is False

    with pytest.raises(ValueError, match="not_owned_or_in_chat"):
        goal_view("goal", owner + 1, chat)
    with db.get_db() as c:
        # Even a stored status string alone is not proof of goal completion.
        c.execute("UPDATE worker_jobs SET state='success'")
    assert goal_view("goal", owner, chat)["goal_completed"] is False


@pytest.mark.parametrize("marker", [0, 1.0])
def test_pending_amendment_cannot_overrule_a_dispatch_marker(storage, marker):
    prepare_goal(storage)
    owner, chat = storage
    with db.get_db() as c:
        payload = json.loads(c.execute("SELECT payload FROM worker_jobs WHERE task_id='consumer'").fetchone()[0])
        payload["dispatch_started_at"] = marker
        original = json.dumps(payload)
        c.execute("UPDATE worker_jobs SET payload=? WHERE task_id='consumer'", (original,))
    with pytest.raises(ValueError, match="task_already_dispatched"):
        WorkerScheduler().amend_pending("consumer", owner, chat, expected_revision=1,
            human_request="Change the document", objective="Changed document")
    assert owned_job("consumer", owner)["payload"] == original
    with db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_task_revisions").fetchone()[0] == 0


def test_pinned_material_pages_keep_full_content_hash_and_contiguous_progress(storage):
    from server.task_summary import get_last_run_summary
    from server.runtime_state import tasks
    from server.controller_shared import pinned_material_pending
    prepare_goal(storage)
    task, report = freeze(storage)
    owner, chat = storage
    consumer = {"task_id":"consumer", "user_id":owner, "chat_id":chat,
                "goal_id":"goal", "device_ids":task["device_ids"]}
    with db.get_db() as c:
        row = c.execute("SELECT * FROM worker_results").fetchone()
        c.execute("UPDATE worker_jobs SET payload=? WHERE task_id='consumer'", (json.dumps(consumer),))
        c.execute("INSERT INTO worker_dependencies VALUES(?,?,?)", ("consumer", "source", row["result_id"]))
        consumer["_pending_pinned_results"] = {row["result_id"]:len(row["content"])}
    tasks["consumer"] = consumer
    try:
        def page(offset):
            return get_last_run_summary(owner, chat_id=chat, exclude_task_id="consumer",
                source_task_id="source", report_offset=offset, allowed_devices={"pc"})
        later = page(4000)
        assert later["status"] == "success"
        assert consumer["_pinned_read_offsets"].get(later["result_id"], 0) == 0
        assert pinned_material_pending(consumer)
        chunks, offset = [], 0
        while offset is not None:
            result = page(offset)
            assert result["sha256"] == row["sha256"]
            chunks.append(result["report_text"])
            offset = result["next_offset"]
        text = "".join(chunks)
        assert text == row["content"]
        assert hashlib.sha256(text.encode()).hexdigest() == row["sha256"]
        assert json.loads(text)["answer"] == task["answer"]
        assert not pinned_material_pending(consumer)
        assert page(len(text))["error"] == "invalid_result_offset"
    finally:
        tasks.pop("consumer", None)

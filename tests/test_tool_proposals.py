import json

import server.controller_non_pipeline as controller_non_pipeline
from server.controller_non_pipeline import process_non_pipeline_command
from server.tool_contracts import get_tool_contract, validate_tool_contract
from server.tool_registry import list_tools


def _tool_call(call_id: str, name: str, args: dict | None = None) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}, ensure_ascii=False),
        },
    }


def _answer_call(call_id: str, text: str, *, basis: list[str] | None = None) -> dict:
    return _tool_call(call_id, "answer_text", {
        "answer_type": "grounded_report" if basis else "pure_text",
        "text": text,
        "basis": basis or [],
        "self_check": {
            "depends_on_current_external_state": bool(basis),
            "claims_completed_action": bool(basis),
            "has_sufficient_evidence": True,
            "missing_evidence_question": "",
        },
    })


def _message(content="", tool_calls=None, finish_reason="tool_calls"):
    msg = {"content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"finish_reason": finish_reason, "message": msg}]}


def _completion_fn(responses):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        assert queue, "Unexpected LLM call"
        return queue.pop(0)

    return _chat_completion_request_fn


def _proposal_payload(name: str = "office.create_docx") -> dict:
    return {
        "name": name,
        "title": "Create DOCX",
        "problem": "Repeated Word document creation through helper scripts is fragile.",
        "purpose": "Create .docx files from structured sections.",
        "category": "office",
        "risk_level": "write",
        "permissions": ["file.write", "office.generate"],
        "input_schema": {"type": "object", "properties": {"sections": {"type": "array"}}},
        "output_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
        "evidence_contract": {"produced": ["file_path", "exists", "size_bytes"]},
        "side_effects": ["writes_file"],
        "idempotency": "not_idempotent",
        "cleanup": "delete generated file if requested",
        "rollback": "delete generated file before external use",
        "examples": [{"input": {"title": "Demo", "sections": []}}],
        "test_plan": ["create small docx", "verify file exists"],
        "priority": "normal",
        "notes": "Proposal only; not executable.",
    }


def test_db_helpers_add_list_get_update_scope_to_user(client):
    from server.database import (
        add_tool_proposal,
        create_user,
        get_tool_proposal,
        list_tool_proposals,
        update_tool_proposal_status,
    )

    owner = create_user("tool-proposal-owner")
    other = create_user("tool-proposal-other")
    proposal_id = add_tool_proposal(user_id=owner["id"], **_proposal_payload())
    add_tool_proposal(user_id=other["id"], **_proposal_payload("html.create_page"))

    owner_items = list_tool_proposals(user_id=owner["id"])
    assert [item["id"] for item in owner_items] == [proposal_id]
    assert get_tool_proposal(proposal_id, user_id=other["id"]) is None

    updated = update_tool_proposal_status(proposal_id, "reviewing", notes="Needs review", user_id=owner["id"])
    assert updated["status"] == "reviewing"
    assert updated["notes"] == "Needs review"

    try:
        update_tool_proposal_status(proposal_id, "approved", user_id=owner["id"])
    except PermissionError as exc:
        assert str(exc) == "proposal_status_requires_admin_review"
    else:
        raise AssertionError("approved status must require admin review")


def test_tool_propose_creates_current_user_proposal(client):
    from server.database import create_user, list_tool_proposals
    from server.tool_proposals import run_tool_proposal_tool

    user = create_user("tool-propose-user")
    result = run_tool_proposal_tool(
        "tool_propose",
        _proposal_payload(),
        user_id=user["id"],
        chat_id=123,
        poll_task_id="poll-1",
    )

    assert result["status"] == "created"
    assert result["proposal_id"]
    proposals = list_tool_proposals(user_id=user["id"])
    assert proposals[0]["name"] == "office.create_docx"
    assert proposals[0]["chat_id"] == 123
    assert proposals[0]["source_poll_task_id"] == "poll-1"


def test_tool_list_proposals_returns_only_current_user(client):
    from server.database import create_user
    from server.tool_proposals import run_tool_proposal_tool

    owner = create_user("tool-list-owner")
    other = create_user("tool-list-other")
    run_tool_proposal_tool("tool_propose", _proposal_payload("office.create_docx"), user_id=owner["id"])
    run_tool_proposal_tool("tool_propose", _proposal_payload("html.create_page"), user_id=other["id"])

    result = run_tool_proposal_tool("tool_list_proposals", {}, user_id=owner["id"])
    assert result["count"] == 1
    assert result["proposals"][0]["name"] == "office.create_docx"


def test_user_cannot_set_implemented_through_tool_update_status(client):
    from server.database import create_user, get_tool_proposal
    from server.tool_proposals import run_tool_proposal_tool

    user = create_user("tool-status-guard-user")
    created = run_tool_proposal_tool("tool_propose", _proposal_payload("office.create_docx"), user_id=user["id"])

    result = run_tool_proposal_tool(
        "tool_update_proposal_status",
        {"proposal_id": created["proposal_id"], "status": "implemented"},
        user_id=user["id"],
    )

    assert result == {
        "status": "error",
        "error": "proposal_status_requires_admin_review",
        "requested_status": "implemented",
    }
    assert get_tool_proposal(created["proposal_id"], user_id=user["id"])["status"] == "proposed"


def test_api_routes_do_not_expose_other_users_proposals(client):
    from server.database import create_user

    owner = create_user("tool-api-owner")
    other = create_user("tool-api-other")
    create_resp = client.post(
        "/api/tool-proposals",
        headers={"X-Token": owner["token"]},
        json=_proposal_payload("office.create_docx"),
    )
    assert create_resp.status_code == 200
    proposal_id = create_resp.json()["proposal_id"]

    other_list = client.get("/api/tool-proposals", headers={"X-Token": other["token"]})
    assert other_list.status_code == 200
    assert other_list.json()["proposals"] == []

    other_get = client.get(f"/api/tool-proposals/{proposal_id}", headers={"X-Token": other["token"]})
    assert other_get.status_code == 404


def test_api_patch_rejects_approved_but_allows_rejected_and_notes(client):
    from server.database import create_user

    user = create_user("tool-api-status-user")
    create_resp = client.post(
        "/api/tool-proposals",
        headers={"X-Token": user["token"]},
        json=_proposal_payload("office.create_docx"),
    )
    assert create_resp.status_code == 200
    proposal_id = create_resp.json()["proposal_id"]

    approved_resp = client.patch(
        f"/api/tool-proposals/{proposal_id}",
        headers={"X-Token": user["token"]},
        json={"status": "approved", "notes": "looks good"},
    )
    assert approved_resp.status_code == 200
    assert approved_resp.json() == {
        "status": "error",
        "error": "proposal_status_requires_admin_review",
        "requested_status": "approved",
    }

    rejected_resp = client.patch(
        f"/api/tool-proposals/{proposal_id}",
        headers={"X-Token": user["token"]},
        json={"status": "rejected", "notes": "user cancelled"},
    )
    assert rejected_resp.status_code == 200
    proposal = rejected_resp.json()["proposal"]
    assert proposal["status"] == "rejected"
    assert proposal["notes"] == "user cancelled"


def test_proposal_name_validation_and_secret_rejection(client):
    from server.database import create_user
    from server.tool_proposals import run_tool_proposal_tool

    user = create_user("tool-validation-user")
    bad_name = run_tool_proposal_tool("tool_propose", _proposal_payload("badname"), user_id=user["id"])
    assert bad_name["status"] == "error"
    assert "namespace.action" in bad_name["error"]

    secret_payload = _proposal_payload("office.create_docx")
    secret_payload["notes"] = "Use API_KEY=secret-value"
    secret = run_tool_proposal_tool("tool_propose", secret_payload, user_id=user["id"])
    assert secret["status"] == "error"
    assert "secrets" in secret["error"]


def test_system_list_tools_includes_public_tooling_tools_compactly():
    registry = list_tools("all")
    payload = json.dumps(registry, ensure_ascii=False)
    tooling_names = {tool["name"] for tool in registry.get("tooling", [])}

    assert {"tool.propose", "tool.list_proposals", "tool.get_proposal"} <= tooling_names
    assert "tool.update_proposal_status" not in tooling_names
    assert "input_schema" not in payload
    assert "properties" not in payload


def test_tool_proposal_contracts_are_valid():
    for name in ("tool.propose", "tool.list_proposals", "tool.get_proposal", "tool.update_proposal_status"):
        contract = get_tool_contract(name)
        assert contract is not None
        assert validate_tool_contract(contract) == []

    assert "tool.proposal.write" in get_tool_contract("tool.propose")["permissions"]
    assert "tool.proposal.read" in get_tool_contract("tool.list_proposals")["permissions"]


def test_can_create_tool_request_can_use_tool_proposal_not_registry_mutation(client, monkeypatch):
    from server.database import create_user, list_tool_proposals

    user = create_user("tool-flow-user")

    async def _audit_ok(**kwargs):
        return True, "valid", False

    monkeypatch.setattr(controller_non_pipeline, "audit_answer_payload", _audit_ok)

    result = __import__("asyncio").run(process_non_pipeline_command(
        user_message="Оформи кандидата на инструмент office.create_docx для Word-документов",
        device_id="device-1",
        device_info={"hostname": "devbox", "os": "Windows"},
        send_command_fn=lambda device_id, action, params: {"status": "ok"},
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=user["id"],
        chat_id=1,
        modes={},
        poll_task_id="poll-tool-proposal",
        cfg={"model": "mock", "max_tokens": 512, "answer_auditor_enabled": True},
        system_msg="system",
        machine_guid=None,
        mem_user_id=str(user["id"]),
        non_pipeline_tools=[],
        max_iterations=3,
        pick_model_fn=lambda cfg, modes: "mock",
        chat_completion_request_fn=_completion_fn([
            _message(tool_calls=[_tool_call("call-propose", "tool_propose", _proposal_payload())]),
            _message(tool_calls=[_answer_call("call-answer", "Кандидат на инструмент оформлен.", basis=["step_1"])]),
        ]),
    ))

    assert result["answer"] == "Кандидат на инструмент оформлен."
    assert [cmd["tool_name"] for cmd in result["commands"]] == ["tool.propose", "answer.text"]
    assert list_tool_proposals(user_id=user["id"])[0]["name"] == "office.create_docx"
    assert "office.create_docx" not in {tool["name"] for tools in list_tools("all").values() for tool in tools}

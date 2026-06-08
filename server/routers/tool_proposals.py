from fastapi import APIRouter, HTTPException, Request

try:
    from ..api_support import get_current_user
    from ..tool_proposals import (
        ToolProposalValidationError,
        create_tool_proposal,
        get_current_user_tool_proposal,
        list_current_user_tool_proposals,
        update_current_user_tool_proposal_status,
    )
except ImportError:
    from api_support import get_current_user  # type: ignore
    from tool_proposals import (  # type: ignore
        ToolProposalValidationError,
        create_tool_proposal,
        get_current_user_tool_proposal,
        list_current_user_tool_proposals,
        update_current_user_tool_proposal_status,
    )


router = APIRouter()


@router.get("/api/tool-proposals")
async def api_list_tool_proposals(request: Request, status: str | None = None, limit: int = 50):
    user = get_current_user(request)
    try:
        return list_current_user_tool_proposals(int(user["id"]), status=status, limit=limit)
    except ToolProposalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/tool-proposals")
async def api_create_tool_proposal(request: Request, payload: dict):
    user = get_current_user(request)
    try:
        return create_tool_proposal(payload, user_id=int(user["id"]), chat_id=payload.get("chat_id"))
    except ToolProposalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/tool-proposals/{proposal_id}")
async def api_get_tool_proposal(proposal_id: int, request: Request):
    user = get_current_user(request)
    result = get_current_user_tool_proposal(proposal_id, int(user["id"]))
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="proposal not found")
    return result


@router.patch("/api/tool-proposals/{proposal_id}")
async def api_update_tool_proposal(proposal_id: int, request: Request, payload: dict):
    user = get_current_user(request)
    try:
        result = update_current_user_tool_proposal_status(
            proposal_id,
            status=str(payload.get("status") or ""),
            notes=payload.get("notes"),
            user_id=int(user["id"]),
        )
    except ToolProposalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="proposal not found")
    return result

"""Retired clients must never start execution or consume a PLAN trial."""
import asyncio

import pytest
from fastapi import HTTPException

from server.routers import tasks as routes


@pytest.mark.parametrize("route", ["run", "review", "decline"])
def test_retired_plan_endpoints_return_gone_without_execution(monkeypatch, route):
    monkeypatch.setattr(routes, "get_current_user", lambda request: {"id": 42})

    async def forbidden(*args, **kwargs):
        raise AssertionError("Retired endpoint reached Worker admission")

    monkeypatch.setattr(routes, "submit_worker", forbidden)

    async def scenario():
        with pytest.raises(HTTPException) as caught:
            if route == "run":
                await routes.api_run_plan(7, routes.RunPlanBody(original_request="old request", confirmed=True), object())
            elif route == "review":
                await routes.api_review_plan("old-task", routes.PlanReviewBody(revision="r1", action="approve"), object())
            else:
                await routes.api_decline_plan_suggestion("old-task", object())
        assert caught.value.status_code == 410

    asyncio.run(scenario())

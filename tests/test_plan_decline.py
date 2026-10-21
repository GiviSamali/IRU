"""Legacy PLAN decline is retired; historical user material remains untouched."""
from server import database as db
from server.runtime_state import tasks


def test_decline_plan_returns_gone_and_preserves_history_and_trial(client):
    user = db.create_user("retired-plan-history")
    auth = client.post("/api/auth", json={"token":user["token"]}).json()
    headers = {"Authorization":f"Bearer {auth['access_token']}"}
    chat = db.create_chat(user["id"], "Historical PLAN")["id"]
    db.add_message(chat, "assistant", "Historical verified result", task_metadata={"_taskId":"old-plan"})
    tasks["old-plan"] = {"task_id":"old-plan", "user_id":user["id"], "chat_id":chat,
                         "message":"old request", "status":"done", "plan_suggestion":"old proposal"}
    before = db.get_messages(chat)
    from server.routers import tasks as routes
    trial = routes.get_plan_trial_used(user["id"])
    response = client.post("/api/tasks/old-plan/decline_plan", headers=headers)
    assert response.status_code == 410
    assert response.json()["detail"] == "execution_mode_unsupported"
    assert tasks["old-plan"]["plan_suggestion"] == "old proposal"
    assert db.get_messages(chat) == before
    assert routes.get_plan_trial_used(user["id"]) == trial
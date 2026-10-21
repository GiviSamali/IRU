import pytest

from server.runtime_state import tasks, devices


def prepare(client, monkeypatch, *, admin=False, used=1):
    from server import database as db
    from server.routers import tasks as routes
    user = db.get_user_by_id(1) if admin else db.create_user("admin")
    chat = db.create_chat(user["id"])
    db.set_plan_trial_used(user["id"], used)
    devices[f"{user['id']}:pc"] = {"user_id": user["id"]}
    tasks["offer"] = {"task_id": "offer", "user_id": user["id"], "chat_id": chat["id"],
                      "status": "done", "message": "Отчёт", "plan_suggestion": "Несколько шагов",
                      "plan_original_request": "Отчёт", "device_ids": [], "created_at": 0, "answer": ""}
    async def no_execution(*args, **kwargs):
        pass
    monkeypatch.setattr(routes, "run_nl_task", no_execution)
    return {"X-Token": user["token"]}, chat["id"], user["id"]


@pytest.mark.parametrize("admin",[False,True])
@pytest.mark.parametrize("body_fields",[
    {}, {"voice_source_task_id":"offer"}, {"source_task_id":"offer"},
    {"source_task_id":"offer","voice_source_task_id":"different"},
    {"device_id":"foreign-or-missing"},
])
def test_retired_plan_is_unsupported_for_buttons_voice_and_admins(client,monkeypatch,admin,body_fields):
    import copy
    from server.routers import tasks as routes
    headers,chat,uid=prepare(client,monkeypatch,admin=admin)
    original=copy.deepcopy(tasks["offer"])
    async def forbidden(*args,**kwargs):pytest.fail("Retired PLAN attempted admission or execution")
    monkeypatch.setattr(routes,"submit_worker",forbidden)
    monkeypatch.setattr(routes,"run_nl_task",forbidden)
    for _ in range(2):
        response=client.post(f"/api/run_plan/{chat}",headers=headers,
            json={"original_request":"Отчёт","confirmed":True,**body_fields})
        assert response.status_code==410
        assert response.json()["detail"]["code"]=="execution_mode_unsupported"
    assert tasks["offer"]==original
    assert not tasks["offer"].get("voice_plan_started")


@pytest.mark.parametrize("admin",[False,True])
def test_historical_plan_offer_does_not_generate_new_voice_consent(client,monkeypatch,admin):
    from server import voice
    headers,chat,uid=prepare(client,monkeypatch,admin=admin)
    async def forbidden(*args,**kwargs):pytest.fail("Historical offer must not synthesize launch consent")
    monkeypatch.setattr(voice,"synthesize",forbidden)
    monkeypatch.setattr(voice,"spoken_parts",forbidden)
    assert client.post("/api/voice/tasks/offer/speech",headers=headers).status_code==204


@pytest.mark.parametrize("revision",["v1","v2"])
def test_retired_plan_revision_speech_is_unsupported_without_tts(client,monkeypatch,revision):
    from server import voice
    headers,chat,uid=prepare(client,monkeypatch,admin=True)
    tasks["offer"].update(status="confirm",plan_suggestion=None,plan_review={
        "revision":"v2","speech":"Historical proposed steps"})
    async def forbidden(*args,**kwargs):pytest.fail("Retired review must not use TTS or summary LLM")
    monkeypatch.setattr(voice,"synthesize",forbidden)
    monkeypatch.setattr(voice,"spoken_parts",forbidden)
    response=client.post(f"/api/voice/tasks/offer/speech?revision={revision}",headers=headers)
    assert response.status_code==410
    assert response.json()["detail"]=="execution_mode_unsupported"
    assert client.post("/api/voice/tasks/offer/speech",headers=headers).status_code==409

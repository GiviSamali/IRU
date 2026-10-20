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


def test_admin_plan_bypasses_exhausted_trial(client, monkeypatch):
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    task = client.get("/api/tasks/offer", headers=headers).json()["task"]
    assert not task.get("plan_trial_used")
    response = client.post(f"/api/run_plan/{chat}", headers=headers,
                           json={"original_request": "Отчёт", "confirmed": True})
    assert response.status_code == 200
    assert tasks[response.json()["task_id"]]["modes"]["autonomous"] is False


def test_name_admin_does_not_grant_admin_plan_access(client, monkeypatch):
    headers, chat, uid = prepare(client, monkeypatch)
    assert uid != 1
    assert client.get("/api/tasks/offer", headers=headers).json()["task"]["plan_trial_used"]
    assert client.post(f"/api/run_plan/{chat}", headers=headers,
                       json={"original_request": "Отчёт", "confirmed": True}).status_code == 403


@pytest.mark.parametrize('source_field', ['voice_source_task_id', 'source_task_id'])
def test_voice_consent_keeps_safety_confirmation_and_is_one_shot(client, monkeypatch, source_field):
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    body = {"original_request": "Отчёт", "confirmed": True, source_field: "offer"}
    response = client.post(f"/api/run_plan/{chat}", headers=headers, json=body)
    assert response.status_code == 200
    assert tasks[response.json()["task_id"]]["modes"] == {"pipeline": True, "autonomous": False}
    assert client.post(f"/api/run_plan/{chat}", headers=headers, json=body).status_code == 409


@pytest.mark.parametrize("change,code", [({"user_id": -1}, 404), ({"chat_id": -1}, 404),
    ({"status": "running"}, 409), ({"plan_declined": True}, 409), ({"plan_suggestion": ""}, 409),
    ({"plan_original_request": "Другой запрос"}, 409)])
@pytest.mark.parametrize('source_field', ['voice_source_task_id', 'source_task_id'])
def test_voice_consent_requires_current_owned_offer(client, monkeypatch, change, code, source_field):
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    tasks["offer"].update(change)
    response = client.post(f"/api/run_plan/{chat}", headers=headers,
        json={"original_request": "Отчёт", "confirmed": True, source_field: "offer"})
    assert response.status_code == code


@pytest.mark.parametrize("admin,expected", [(True, "Запустить?"), (False, "Пробный запуск")])
def test_plan_offer_speech_without_summary_llm(client, monkeypatch, admin, expected):
    from server import voice
    headers, chat, uid = prepare(client, monkeypatch, admin=admin)
    monkeypatch.setenv("YANDEX_API_KEY", "test-only")
    spoken = []
    async def synthesize(text):
        spoken.append(text)
        return b"ogg"
    async def forbidden(*args):
        raise AssertionError("Plan offer must not call summary LLM")
    monkeypatch.setattr(voice, "synthesize", synthesize)
    monkeypatch.setattr(voice, "spoken_parts", forbidden)
    response = client.post("/api/voice/tasks/offer/speech", headers=headers)
    assert response.status_code == 200
    assert expected in spoken[0]


def test_draft_plan_speech_is_short_revision_bound_and_keeps_question(client, monkeypatch):
    from server import voice
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    tasks["offer"].update(status="confirm", plan_suggestion=None, plan_review={
        "revision": "v2", "speech": "Предлагаю план. 1. Презентация. 2. Word. 3. Excel. Хотите что-то изменить?"})
    monkeypatch.setenv("YANDEX_API_KEY", "test-only")
    spoken = []
    async def synthesize(text): spoken.append(text); return b"ogg"
    async def forbidden(*args): pytest.fail("draft plan must not use final-answer summarization")
    monkeypatch.setattr(voice, "synthesize", synthesize)
    monkeypatch.setattr(voice, "spoken_parts", forbidden)
    assert client.post("/api/voice/tasks/offer/speech?revision=v1", headers=headers).status_code == 409
    response = client.post("/api/voice/tasks/offer/speech?revision=v2", headers=headers)
    assert response.status_code == 200
    assert spoken == [tasks["offer"]["plan_review"]["speech"]]
    assert spoken[0].endswith("Хотите что-то изменить?")


def test_ow_plan_offer_never_ignores_explicit_invalid_device(client, monkeypatch):
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    tasks['offer']['device_ids']=[f'{uid}:device-1']
    response=client.post(f'/api/run_plan/{chat}',headers=headers,json={
        'original_request':'Отчёт','confirmed':True,'voice_source_task_id':'offer','device_id':'foreign-or-missing'})
    assert response.json()=={'status':'error','error':'device_not_owned_or_unavailable'}
    assert not tasks['offer'].get('voice_plan_started')


def test_button_plan_keeps_original_sources_and_objective(client, monkeypatch):
    from server.routers import tasks as routes
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    tasks['offer'].update(orchestrated=True, source_task_ids=['saved-research'],
                          proposed_objective='Prepare remaining documents', proposed_context_summary='Reuse completed research')
    captured = []
    async def submit(*args, **kwargs):
        captured.append((args, kwargs))
        return {'task_id': 'new-continuation', 'status': 'queued'}
    monkeypatch.setattr(routes, 'submit_worker', submit)
    response = client.post(f'/api/run_plan/{chat}', headers=headers, json={
        'original_request': 'Отчёт', 'confirmed': True, 'source_task_id': 'offer'})
    assert response.status_code == 200
    args, handoff = captured[0]
    assert args[2] == 'Отчёт'
    assert handoff['source_task_ids'] == ['saved-research']
    assert handoff['objective'] == 'Prepare remaining documents'
    assert handoff['context_summary'] == 'Reuse completed research'
    assert handoff['request_key'] == 'plan:offer'


def test_plan_rejects_conflicting_source_aliases(client, monkeypatch):
    headers, chat, uid = prepare(client, monkeypatch, admin=True)
    response = client.post(f'/api/run_plan/{chat}', headers=headers, json={
        'original_request': 'Отчёт', 'confirmed': True, 'source_task_id': 'offer', 'voice_source_task_id': 'different'})
    assert response.status_code == 400

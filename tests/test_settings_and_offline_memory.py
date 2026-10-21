import copy
import json
import time
import pytest
from server import database as db
from server import controller_onboarding as onboarding


def login(client,user):
    token=client.post("/api/auth",json={"token":user["token"]}).json()["access_token"]
    return {"Authorization":"Bearer "+token}


def wait(client,headers,tid):
    for _ in range(40):
        task=client.get(f"/api/tasks/{tid}",headers=headers).json()["task"]
        if task["status"] not in {"running","pending","queued","waiting_dependency","waiting_confirmation","waiting_input"}:return task
        time.sleep(.02)
    raise AssertionError("Task did not complete")


@pytest.mark.parametrize("name,text,allowed",[("remember_fact","Запомни, что люблю чай",True),("remember_fact","Объясни слово «запомни»",False),("memory_list_facts","Что ты помнишь обо мне?",True),("forget_fact","Забудь факт о чае",True)])
def test_no_device_memory_runs_through_authenticated_chat(client,monkeypatch,name,text,allowed):
    owner=db.create_user("no-device-memory")
    other=db.create_user("other-memory")
    headers=login(client,owner)
    own_fact=db.add_user_fact(str(owner["id"]),"owner preference","preference")
    db.add_user_fact(str(other["id"]),"OTHER_PRIVATE_FACT","preference")
    args={"text":"prefers tea","category":"preference"} if name=="remember_fact" else {"fact_id":own_fact,"source":"user"} if name=="forget_fact" else {"limit":20}
    terminal={"answer_type":"partial_report","text":"Данные памяти обработаны; полнота результата отдельно не подтверждена.",
        "basis":["step_1"],"self_check":{"depends_on_current_external_state":True,"claims_completed_action":False,
            "has_sufficient_evidence":False,"missing_evidence_question":"Полный результат не проверен"}}
    replies=iter([{"tool_calls":[{"id":"memory-call","function":{"name":name,"arguments":json.dumps(args)}}]},
        {"tool_calls":[{"id":"terminal","function":{"name":"answer_text","arguments":json.dumps(terminal)}}]}])
    requests=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {"choices":[{"message":next(replies)}]}
    class Provider:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,**kw):requests.append(copy.deepcopy(kw["json"]));return Response()
    from server import controller, orchestrator as orch
    async def semantic_decision(message, context, **kwargs):
        assert message==text
        if not allowed:
            return orch.Decision(intent="conversation",answer="Обсуждение слова не изменяет память"),{}
        proofs=[{"operation":"remember_fact","human_quote":text}] if name=="remember_fact" else []
        return orch.Decision(intent="delegate",objective=text,scope="server",memory_authorizations=proofs),{}
    monkeypatch.setattr(orch,"decide",semantic_decision)
    monkeypatch.setattr(controller,"load_llm_config",lambda:{"model":"mock","base_url":"https://fixture.invalid","api_key":"test"})
    monkeypatch.setattr(onboarding.httpx,"AsyncClient",Provider)
    response=client.post("/api/chat",headers=headers,json={"message":text})
    assert response.status_code==200
    if not allowed:
        assert not response.json().get("worker_task_id") and not requests
        assert [fact["fact_text"] for fact in db.get_user_facts(str(owner["id"]))]==["owner preference"]
        return
    worker_id=response.json()["worker_task_ids"][0]
    task=wait(client,headers,worker_id)
    assert task["device_ids"]==[]
    assert task["worker_report"]["goal_completed"] is False
    assert task["worker_report"]["status"]=="partial"
    names={t["function"]["name"] for t in requests[0]["tools"]}
    assert {"memory_get_stats","memory_list_facts","remember_fact","forget_fact"} <= names
    assert "OTHER_PRIVATE_FACT" not in json.dumps(requests)
    if name=="remember_fact":assert any(f["fact_text"]=="prefers tea" for f in db.get_user_facts(str(owner["id"])))
    if name=="forget_fact":assert db.get_user_facts(str(owner["id"]))==[]
    assert len(db.get_user_facts(str(other["id"])))==1
    assert task["memory_stats"] is not None


def test_facts_crud_api_and_settings_are_available_without_device(client):
    owner=db.create_user("offline-settings")
    headers=login(client,owner)
    response=client.post("/api/memory/facts",headers=headers,json={"text":"Люблю чай"})
    assert response.status_code==200
    fact_id=response.json()["fact"]["id"]
    assert client.get("/api/memory/facts",headers=headers).json()["facts"][0]["text"]=="Люблю чай"
    assert client.delete(f"/api/memory/facts/{fact_id}",headers=headers).status_code==200
    from pathlib import Path
    html=(Path(__file__).parents[1]/"ui/index.html").read_text(encoding="utf-8")
    settings=html[html.index('<aside class="memory-panel" id="settingsPanel"'):html.index('<aside class="memory-panel" id="memoryPanel"')]
    facts=html[html.index('<aside class="memory-panel" id="memoryPanel"'):html.index('<!-- ADMIN PANEL -->')]
    assert all(label in settings for label in ("Настройки","Об ИРУ","Инструкция","Соглашение","browserConnectionStatus"))
    assert 'memoryFactInput' not in settings and 'memoryPanelList' not in settings
    assert 'Факты' in facts and 'memoryFactInput' in facts
    assert 'settings-links' not in facts and 'browserConnectionStatus' not in facts
    header=html[html.index('id="headerActions"'):html.index('id="chatMessages"')]
    assert 'href="/about"' not in header and 'href="/terms"' not in header


def test_cost_and_tokens_visible_only_to_real_admin(client):
    normal=db.create_user("Admin")
    headers=login(client,normal)
    assert client.get("/api/user_info",headers=headers).json()["user"]["is_admin"] is False
    chat=db.create_chat(normal["id"])
    for path in ("/api/usage/summary",f"/api/chats/{chat['id']}/usage","/api/tasks/test/usage"):
        response=client.get(path,headers=headers)
        assert response.status_code==403 and "summary" not in response.json()
    admin=db.get_user_by_id(1)
    headers=login(client,admin)
    assert client.get("/api/user_info",headers=headers).json()["user"]["is_admin"] is True
    assert client.get("/api/usage/summary",headers=headers).status_code==200

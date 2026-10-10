import asyncio
import json
import time
from server import database as db
from server.runtime_state import tasks
from server.worker_scheduler import init_worker_storage


def test_operations_owner_fifo_and_restore(client):
    a=db.create_user('dock-a');b=db.create_user('dock-b')
    chat=db.create_chat(a['id'],'a')['id'];other=db.create_chat(b['id'],'b')['id']
    init_worker_storage()
    with db.get_db() as c:
        for n,(owner,cid,status) in enumerate([(a['id'],chat,'running'),(a['id'],chat,'queued'),(a['id'],chat,'queued'),(b['id'],other,'running')]):
            task={'task_id':f'dock-{n}','user_id':owner,'chat_id':cid,'message':f'job-{n}', 'device_ids':[f'{owner}:pc'],'kind':'worker','worker_id':'worker-1','status':status,'created_at':n+1,'modes':{},'commands':[]}
            c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(task['task_id'],owner,cid,status,json.dumps(task),n+1,n+1))
    # Neither runtime presence nor current chat is needed for the own persisted queue.
    response=client.get('/api/operations',headers={'X-Token':a['token']})
    assert response.status_code==200
    rows=response.json()['operations']
    assert [r['task_id'] for r in rows]==['dock-0','dock-1','dock-2']
    assert rows[0]['status']=='running' and all(r['can_cancel'] for r in rows)
    assert all('payload' not in r and 'commands' not in r for r in rows)
    assert client.get('/api/tasks/dock-3',headers={'X-Token':a['token']}).status_code==404
    assert client.get('/api/operations').status_code in {401,403}
    assert client.get('/api/operations',headers={'X-Token':b['token']}).json()['operations'][0]['task_id']=='dock-3'

def test_retired_highlights_do_not_persist_or_reach_api(client,monkeypatch):
    from types import SimpleNamespace
    from server import orchestrator as orch
    user=db.create_user('highlight-owner');chat=db.create_chat(user['id'],'dialog')['id']
    answer='LAN соединяет устройства рядом.'
    async def decide(*args,**kwargs):
        return orch.Decision(intent='conversation',answer=answer,highlights=[{'start':0,'end':3,'kind':'definition'},{'start':10,'end':999,'kind':'result'}]),{}
    async def forbidden(*args,**kwargs):raise AssertionError('Formatting grants no execution')
    monkeypatch.setattr(orch,'decide',decide)
    cmd=SimpleNamespace(message='Что такое LAN?',request_id='highlight-turn',device_id='',modes={},broadcast=False)
    result=asyncio.run(orch.run_turn(cmd,user,chat,forbidden))
    row=db.get_messages(chat)[-1]
    assert row['content']==answer and 'highlights' not in row
    restored=orch.restore_dialogue(result['task_id'],user['id']);assert 'highlights' not in restored
    view=client.get('/api/tasks/'+result['task_id'],headers={'X-Token':user['token']}).json()['task']
    assert 'highlights' not in view and view['answer']==answer

"""Actual Orchestrator HTTP material, controlled decisions; no LIVE model/device."""
import asyncio
import copy
import json
import time

import httpx

from server import controller, database as db, orchestrator
from test_orchestrator_worker import owners


def test_short_open_receives_last_confirmed_pptx_in_actual_request(owners, monkeypatch):
    owner,_=owners
    path='C:/Desktop/AI-news.pptx'
    artifact={'type':'file','path':path,'name':'AI-news.pptx','device_id':'pc',
        'verified':True,'sha256':'a'*64,'size_bytes':1024,'source_task_id':'created-pptx',
        'artifact_role':'final','format':'pptx'}
    report={'task_id':'created-pptx','status':'success','goal_completed':True,
        'summary':'Created AI-news.pptx on the desktop','artifacts':[artifact]}
    payload={'task_id':'created-pptx','user_id':owner['id'],'chat_id':owner['chat_id'],
        'original_request':'Create a presentation','message':'Create a presentation',
        'device_ids':['pc'],'worker_report':report,'commands':[],'answer':report['summary']}
    with db.get_db() as c:
        c.execute('INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('created-pptx',owner['id'],owner['chat_id'],'success',json.dumps(payload),json.dumps(report),None,time.time(),time.time(),None))
        for role,text in [('user','Create a presentation'),('assistant','Created AI-news.pptx on your desktop.')]:
            c.execute('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',
                (owner['chat_id'],role,text,time.time()))
    cfg={'model':'mock','base_url':'https://provider.invalid','api_key':'fake-only',
        'unknown_model_input_tokens':1000000,'max_tokens':2400}
    monkeypatch.setattr(orchestrator,'load_llm_config',lambda:cfg)
    requests=[]
    encode=json.dumps

    class FakeHTTP:
        async def post(self,url,*,json,headers):
            requests.append(copy.deepcopy(json))
            choice={'intent':'delegate','objective':'Open the created presentation',
                'target_device_ids':['pc'],'source_task_ids':['created-pptx'],
                'assignments':[{'key':'open','objective':'Request opening '+path,
                    'artifacts':[],'window_effects':['open'],
                    'open_objects':[{'type':'file','path':path,'device_id':'pc'}]}]}
            body={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'id':'decision',
                'function':{'name':'orchestrator_decision','arguments':encode(choice)}}]}}]}
            return httpx.Response(200,json=body,request=httpx.Request('POST',url))

    async def complete(client,cfg,model,messages,**kwargs):
        return await controller._chat_completion_request(FakeHTTP(),cfg,model,messages,**kwargs)

    monkeypatch.setattr(orchestrator,'_chat_completion_request',complete)
    context=orchestrator.context_for(owner['id'],owner['chat_id'],'Open it','pc')
    decision,stats=asyncio.run(orchestrator.decide('Open it',context,
        user_id=owner['id'],chat_id=owner['chat_id'],task_id='short-open'))
    assert len(requests)==stats['llm_calls']==1
    material=json.loads(requests[0]['messages'][1]['content'])['untrusted_context']
    assert material['history'][-1]['content']=='Created AI-news.pptx on your desktop.'
    assert material['tasks'][0]['report']['artifacts']==[artifact]
    assert requests[0]['messages'][-1]['content']=='Open it'
    assert decision.source_task_ids==['created-pptx']
    assert decision.assignments[0].open_objects[0].path==path
    assert decision.assignments[0].artifacts==[]

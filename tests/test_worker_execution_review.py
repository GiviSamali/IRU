"""Three bounded execution fixes; fake providers/transports only."""
import asyncio
import json
import pytest
from server import database as db, task_runtime as runtime
from server.api_support import assess_action, is_recovery_observation
from server.runtime_state import tasks
from server.worker_reports import build_worker_report
from test_tool_only_protocol import _run_case, _message, _tool_call
from test_ff01_worker_execution import FileAgent, PROJECT, replacement

PPTX_COMMAND = "Get-ChildItem -LiteralPath 'C:/Desktop' -Filter '*.pptx' -File | Select-Object FullName,Name | ConvertTo-Json -Compress"


@pytest.fixture
def local_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'review.sqlite')
    db.init_db()


@pytest.mark.parametrize('command',[
    'Get-ChildItem', PPTX_COMMAND,
    'Get-ChildItem -Path "C:/My Documents" -File',
    "Get-Content -LiteralPath 'C:/log.txt' -Tail 20",
    "Get-FileHash -LiteralPath 'C:/report.pptx' -Algorithm SHA256",
    "Test-Path -LiteralPath 'C:/report.pptx' -PathType Leaf",
    "Get-ChildItem -Filter 'Remove-Item*.txt'",
])
def test_closed_literal_read_does_not_ask_confirmation(command):
    params={'command':command,'shell':'powershell','artifact_paths':[]}
    assert assess_action('execute_cmd',params)['decision']=='ALLOW'
    assert is_recovery_observation('execute_cmd',params)


@pytest.mark.parametrize('command',[
    'Get-ChildItem; Remove-Item x', 'Get-ChildItem | Remove-Item',
    'Get-ChildItem > x.txt','Get-ChildItem $(Remove-Item x)',
    'Get-ChildItem -Path "$(Remove-Item x)"',
    'Get-ChildItem -Filter @{x=1}', 'Get-ChildItem -Path $env:HOME',
    'Get-ChildItem `\nRemove-Item x', 'Get-ChildItem\nNew-Item x',
    'Get-ChildItem\u2028Clear-History', 'Get-ChildItem | % { Remove-Item x }',
    'Get-ChildItem -OutVariable x', 'Get-ChildItem -ErrorAction (Invoke-Expression x)',
    'Get-ChildItem | Select-Object @{Name="x";Expression={Remove-Item x}}',
    'Get-ChildItem | ConvertTo-Json | Invoke-Expression',
    'Get-Content -Path HKLM:/Software', 'Get-Content -Path C:/x:stream',
    'Get-ChildItem -LiteralPath "C:/x\\";Remove-Item x;"',
    'gci', 'python readonly.py', 'powershell -EncodedCommand abc',
])
def test_unknown_grammar_never_becomes_read_only(command):
    params={'command':command,'artifact_paths':[]}
    # Automatic execution permission never makes opaque code a read-only observation.
    assert not is_recovery_observation('execute_cmd',params)


@pytest.mark.parametrize('extra',[{'shell':'cmd'},{'shell':'bash'},
    {'artifact_paths':['C:/output.txt']},{'preserve_content_paths':['C:/report.pptx']}])
def test_shell_or_artifact_declaration_cannot_extend_read_grammar(extra):
    assert assess_action('execute_cmd',{'command':PPTX_COMMAND,**extra})['decision']=='ALLOW'
    assert not is_recovery_observation('execute_cmd',{'command':PPTX_COMMAND,**extra})


def test_regular_worker_has_no_automatic_assessor_entrypoint():
    import server.controller_non_pipeline as loop
    assert not hasattr(loop,'assess_result_coverage')



def test_powershell_names_are_not_safe_native_programs_on_other_os():
    params={'command':PPTX_COMMAND,'artifact_paths':[]}
    assert assess_action('execute_cmd',params,inspection={'os':'Linux'})['decision']=='ALLOW'
    assert not is_recovery_observation('execute_cmd',params,device_os='Linux')


def test_native_literal_read_skips_confirmation_but_not_ownership(local_db,monkeypatch):
    sent=[]
    class Socket:
        async def send_text(self,text):
            payload=json.loads(text)['payload'];sent.append(payload)
            runtime.devices['1:pc']['pending'].pop(payload['id']).set_result(
                {'returncode':0,'stdout':'[]','stderr':''})
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**k:None)
    monkeypatch.setitem(runtime.devices,'1:pc',{'user_id':1,'info':{'os':'Windows'},'ws':Socket(),'pending':{}})
    async def scenario():
        result=await runtime.send_command_to_agent('1:pc','execute_cmd',{'command':PPTX_COMMAND,'artifact_paths':[]},user_id=1)
        assert result['returncode']==0
        with pytest.raises(RuntimeError,match='target_device_not_found'):
            await runtime.send_command_to_agent('1:pc','execute_cmd',{'command':PPTX_COMMAND},user_id=2)
        result=await runtime.send_command_to_agent('1:pc','execute_cmd',{'command':'Get-ChildItem | Set-Content C:/x.txt'},user_id=1)
        assert result['returncode']==0
    asyncio.run(scenario())
    assert len(sent)==2


def test_cli_diagnostic_result_is_owned_by_worker(local_db,monkeypatch):
    task={'task_id':'diagnostic','device_ids':['device-1'],'execution_scope':'general',
          'proposed_objective':'Interpret the diagnostic output'}
    monkeypatch.setitem(tasks,'diagnostic',task)
    seen=[]
    result=_run_case([_message(tool_calls=[_tool_call('run','execute_cmd',{'command':'python probe.py'})]),
                      answer('Diagnostic conclusion from the observed facts')],poll_task_id='diagnostic',captured=seen,
                      cfg={'model':'mock','answer_auditor_enabled':True})
    assert len(seen)==2 and not any(x.get('phase')=='result_coverage' for x in seen)
    assert build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})['goal_completed']



def test_preservation_flag_alone_does_not_allow_overwrite(local_db,monkeypatch):
    task={'task_id':'t','preserve_file_bytes':True,'assigned_artifacts':['C:/x.txt']}
    monkeypatch.setitem(tasks,'t',task)
    sent=[]
    async def send(*args):sent.append(args);return {'status':'ok'}
    result=_run_case([_message(tool_calls=[_tool_call('bad','write_content',{'path':'C:/x.txt','content':'Written'})]),
                      answer('Not changed')],poll_task_id='t',send_command_fn=send)
    assert not sent and result['commands'][0]['result']['dispatched'] is False



def answer(text='Observed'):
    return _message(tool_calls=[_tool_call('final','answer_text',{'result':text,'unresolved':[]})])


@pytest.mark.parametrize('scope,expected_assessments',[('general',0),('analysis',0)])
def test_pptx_search_counts_all_llm_calls(local_db,monkeypatch,record_property,scope,expected_assessments):
    task={'task_id':'find','execution_scope':scope,'proposed_objective':'Find PPTX on Desktop',
          'original_request':'Find PPTX on Desktop','device_ids':['device-1']}
    monkeypatch.setitem(tasks,'find',task)
    async def send(device,action,params):
        assert assess_action(action,params)['decision']=='ALLOW'
        return {'returncode':0,'stdout':json.dumps([{'FullName':'C:/Desktop/a.pptx','Name':'a.pptx'}]),'stderr':''}
    replies=[_message(tool_calls=[_tool_call('find','execute_cmd',{'command':PPTX_COMMAND})]),answer('C:/Desktop/a.pptx')]
    if expected_assessments:
        replies.append(_message(content=json.dumps({'coverage':'complete','evidence_refs':['step_1'],'reason':'Observed result matches requested listing'}),finish_reason='stop'))
    captured=[]
    result=_run_case(replies,poll_task_id='find',user_message=task['original_request'],send_command_fn=send,
        captured=captured,cfg={'model':'fake','answer_auditor_enabled':True})
    assessments=sum(c.get('phase')=='result_coverage' for c in captured)
    assert assessments==expected_assessments and len(captured)-assessments==2
    assert len([c for c in result['commands'] if c.get('tool_name')=='execute_cmd'])==1
    report=build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})
    # The successful listing plus the Worker terminal result completes this Assignment.
    assert report['status']=='success'
    record_property('worker_llm_calls',2);record_property('assessor_calls',assessments)
    record_property('total_llm_calls',len(captured))
    record_property('goal_status',report['status'])


@pytest.mark.parametrize('stale_first',[False,True])
def test_confirmed_exact_edit_does_not_reassess_bytes(local_db,monkeypatch,stale_first):
    agent=FileAgent();captured=[]
    task={'task_id':'exact','execution_scope':'general','preserve_file_bytes':True,
          'proposed_objective':'Replace draft with ready','assigned_artifacts':[PROJECT],'device_ids':['device-1']}
    monkeypatch.setitem(tasks,'exact',task)
    replies=([_message(tool_calls=[_tool_call('stale','write_content',{
        **replacement(),'expected_sha256':'0'*64})])] if stale_first else [])+[
        _message(tool_calls=[_tool_call('read','read_file',{'path':PROJECT})]),
        _message(tool_calls=[_tool_call('write','write_content',replacement())]),answer('Changed')]
    result=_run_case(replies,
        poll_task_id='exact',send_command_fn=agent.send,device_info={'os':'Windows','file_replace_protocol':2},
        captured=captured,cfg={'model':'fake','answer_auditor_enabled':True})
    assert len(captured)==3+int(stale_first) and not any(c.get('phase')=='result_coverage' for c in captured)
    assert [a for _,a,_ in agent.sent]==(['write_content'] if stale_first else [])+['get_file_content','write_content']


def test_pptx_find_and_open_needs_no_assessor_when_object_is_proven(local_db,monkeypatch,record_property):
    path='C:/Desktop/a.pptx'
    task={'task_id':'open','execution_scope':'general','proposed_objective':'Find and open a.pptx',
          'original_request':'Find and open a.pptx','device_ids':['device-1'],
          'window_effects':['open'],'open_objects':[{'type':'file','path':path}]}
    monkeypatch.setitem(tasks,'open',task);captured=[]
    async def send(device,action,params):
        assert assess_action(action,params)['decision']=='ALLOW'
        if action=='execute_cmd':return {'returncode':0,'stdout':json.dumps([path]),'stderr':''}
        return {'status':'success','completion_state':'success','requested_object':{'type':'file','path':path},
            'object_open_verified':True,'observed_object':{'observed_path':path,'title':'a - PowerPoint',
                'process':'powerpnt.exe','class_name':'PPTFrameClass'}}
    result=_run_case([
        _message(tool_calls=[_tool_call('find','execute_cmd',{'command':PPTX_COMMAND})]),
        _message(tool_calls=[_tool_call('open','app_launch',{'command':path})]),answer('Opened')],
        poll_task_id='open',send_command_fn=send,captured=captured,cfg={'model':'fake','answer_auditor_enabled':True})
    report=build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})
    assert report['goal_completed'] and len(captured)==3
    assert not any(c.get('phase')=='result_coverage' for c in captured)
    record_property('worker_llm_calls',3);record_property('assessor_calls',0);record_property('total_llm_calls',3)


@pytest.mark.parametrize('name,args',[
    ('list_dir',{'path':'C:/'}),('read_file',{'path':'C:/file.txt'}),
    ('window_control',{'action':'list'}),('execute_cmd',{'command':PPTX_COMMAND}),
])
def test_unknown_read_does_not_freeze_next_authorized_write(local_db,name,args):
    sent=[];agent=FileAgent()
    async def send(device,action,params):
        sent.append(action)
        if action!='write_content':return {'status':'unknown','error':'observation_timeout'}
        return await agent.send(device,action,params)
    result=_run_case([_message(tool_calls=[_tool_call('observe',name,args),
        _tool_call('write','write_content',{'path':PROJECT,'content':'next'})]),answer('Write performed')],
        send_command_fn=send,device_info={'os':'Windows','file_replace_protocol':2})
    assert sent[-1]=='write_content' and len(agent.sent)==1
    assert not any(c.get('result',{}).get('error')=='mutation_blocked_after_unknown' for c in result['commands'])


@pytest.mark.parametrize('first_action,expect_locked',[('list_dir',False),('write_content',True)])
def test_runtime_unknown_flag_tracks_possible_side_effect(client,monkeypatch,first_action,expect_locked):
    from test_browser_runtime_guards import setup_runtime
    user,key,chat=setup_runtime(client,monkeypatch);sent=[];observed=[]
    async def transport(target,action,params,**kwargs):
        sent.append(action)
        if len(sent)==1:raise runtime.AgentOutcomeUnknown('response_lost')
        return {'status':'ok','verified':True,'committed':True,'path':'C:/x.txt','file_sha256':'a'*64}
    async def process(**kwargs):
        observed.append(await kwargs['send_command_fn']('givi',first_action,{'path':'C:/x.txt','content':'x'} if first_action=='write_content' else {'path':'C:/'}))
        observed.append(await kwargs['send_command_fn']('givi','write_content',{'path':'C:/x.txt','content':'x'}))
        return {'answer':'Observed outcomes','commands':[],'tasks':[]}
    async def no_probe(**kwargs):pass
    monkeypatch.setattr(runtime,'send_command_to_agent',transport)
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',no_probe)
    asyncio.run(runtime.run_nl_task('guard-task',user['id'],'Inspect then write',[key],chat))
    assert bool(tasks['guard-task'].get('_action_outcome_unknown'))==expect_locked
    assert len(sent)==(1 if expect_locked else 2)
    assert (observed[-1].get('status')=='blocked')==expect_locked

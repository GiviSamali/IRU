import pytest
from server import database as db
from server.tool_repeat_guard import unchanged_command_evidence, command_observation_key, find_prior_successful_read_only_tool_step, mark_read_only_tool_step
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call


@pytest.fixture(autouse=True)
def isolated_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'case.sqlite')
    db.init_db()


def call(command='inspect',contract=None):
    args={'command':command,'artifact_paths':[]}
    if contract:args['observation_contract']=contract
    return _message(tool_calls=[_tool_call('x','execute_cmd',args)])


def entry(command='inspect',out='MISMATCH=188',device='pc'):
    return {'step_id':'step_1','tool_name':'execute_cmd','command':command,
        'target_device_id':device,'status':'success','result':{'returncode':0,'stdout':out,'stderr':''}}


@pytest.mark.parametrize('mode',['wait','monitor','measurement'])
def test_bounded_repeat_contract(mode):
    journal=[entry(),entry()];args={'command':'inspect','artifact_paths':[],
        'observation_contract':{'mode':mode,'max_samples':3,'condition':'until changed','human_quote':'три измерения'}}
    assert unchanged_command_evidence(journal,args,'pc','Сделай три измерения') is None
    assert unchanged_command_evidence(journal+[entry()],args,'pc','Сделай три измерения') is not None
    assert unchanged_command_evidence([entry(out=str(i)) for i in range(3)],args,'pc','Сделай три измерения') is None
    if mode=='measurement':
        assert unchanged_command_evidence(journal,args,'pc','Другая просьба') is not None


def test_changes_and_mutations_invalidate_observations():
    args={'command':'inspect','artifact_paths':[]}
    assert unchanged_command_evidence([entry(),entry()],args,'pc','')
    assert not unchanged_command_evidence([entry(),entry(out='new')],args,'pc','')
    assert not unchanged_command_evidence([entry(),entry('change'),entry()],args,'pc','')
    assert not unchanged_command_evidence([entry(),entry(device='other')],args,'pc','')
    e=entry();e['result']['artifact_receipts']=[{'verified':True}]
    assert not unchanged_command_evidence([e,e],args,'pc','')
    e=entry();e['status']='failed'
    assert not unchanged_command_evidence([entry(),e],args,'pc','')
    e=entry();e['result']['status']='unknown'
    assert not unchanged_command_evidence([entry(),e],args,'pc','')
    assert not unchanged_command_evidence([entry(),entry()],{**args,'artifact_paths':['C:/changed.txt']},'pc','')


def test_state_change_invalidates_native_cache_and_defaults_do_not_evade_command_key():
    args={'command':'inspect','artifact_paths':[]}
    assert command_observation_key(args)==command_observation_key({**args,'long_running':False,'shell':'auto','timeout':30})
    assert command_observation_key(args)!=command_observation_key({**args,'shell':'cmd'})
    first={'tool_name':'memory.list_facts','status':'success','result':{'facts':[]}}
    mark_read_only_tool_step(first,'memory.list_facts',{})
    assert find_prior_successful_read_only_tool_step([first],'memory.list_facts',{}) is first
    assert not find_prior_successful_read_only_tool_step([first,{'tool_name':'remember_fact','status':'success'}],'memory.list_facts',{})


def test_third_call_reuses_proof_then_original_goal_continues():
    sent=[];captured=[]
    async def send(device,action,args):
        assert 'observation_contract' not in args
        sent.append(args['command'])
        return {'returncode':0,'stdout':'MISMATCH=188' if args['command']=='inspect' else 'done','stderr':''}
    goal='Copy library, launch page, verify rendering, clean temporary files'
    responses=[call(),call(),call(),call('launch page'),call('verify rendering'),call('cleanup'),
        _message(tool_calls=[_answer_call('answer','Partial report',answer_type='partial_report',basis=['step_1'])])]
    result=_run_case(responses,user_message=goal,send_command_fn=send,captured=captured,max_iterations=12)
    assert sent==['inspect','inspect','launch page','verify rendering','cleanup']
    assert len([c for c in result['commands'] if c.get('tool_name')=='execute_cmd'])==5
    messages=captured[3]['messages']
    assert any('reused_observation' in str(m.get('content')) and 'step_2' in str(m.get('content')) for m in messages)
    assert any(goal in str(m.get('content')) and 'DIFFERENT' in str(m.get('content')) for m in messages)


def test_stubborn_repeat_stops_early_without_goal_success():
    sent=[];captured=[]
    async def send(*args):
        sent.append(1);return {'returncode':0,'stdout':'unchanged','stderr':''}
    result=_run_case([call()]*4,send_command_fn=send,captured=captured,max_iterations=20)
    assert len(sent)==2 and len(captured)==4
    assert result['task_receipt']['terminal_reason']=='no_progress'
    assert result['task_receipt']['goal_completed'] is False


def test_wait_contract_is_stripped_and_can_sample_more_than_twice():
    sent=[]
    async def send(device,action,args):
        assert 'observation_contract' not in args
        sent.append(1);return {'returncode':0,'stdout':'pending','stderr':''}
    contract={'mode':'wait','max_samples':3,'condition':'until ready'}
    result=_run_case([call(contract=contract)]*5,send_command_fn=send,max_iterations=20)
    assert len(sent)==3 and result['task_receipt']['terminal_reason']=='no_progress'

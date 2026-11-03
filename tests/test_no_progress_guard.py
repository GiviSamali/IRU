import pytest
from server import database as db
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call


@pytest.fixture(autouse=True)
def isolated_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'case.sqlite')
    db.init_db()


def call(command='inspect'):
    return _message(tool_calls=[_tool_call('x','execute_cmd',{'command':command,'artifact_paths':[]})])


def test_third_observation_executes_and_original_goal_continues():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(args['command'])
        return {'returncode':0,'stdout':'MISMATCH=188' if args['command']=='inspect' else 'done','stderr':''}
    goal='Copy library, launch page, verify rendering, clean temporary files'
    responses=[call(),call(),call(),call('launch page'),call('verify rendering'),call('cleanup'),
        _message(tool_calls=[_answer_call('answer','Partial report',answer_type='partial_report',basis=['step_1'])])]
    result=_run_case(responses,user_message=goal,send_command_fn=send,captured=captured,max_iterations=12)
    assert sent==['inspect','inspect','inspect','launch page','verify rendering','cleanup']
    assert len([c for c in result['commands'] if c.get('tool_name')=='execute_cmd'])==6
    messages=captured[3]['messages']
    assert any('step_3' in str(m.get('content')) for m in messages)
    assert any(goal in str(m.get('content')) for m in messages)


def test_repeated_observation_does_not_force_early_stop():
    sent=[];captured=[]
    async def send(*args):
        sent.append(1);return {'returncode':0,'stdout':'unchanged','stderr':''}
    result=_run_case([call()]*4+[_message(tool_calls=[_tool_call('end','answer_text',
        {'result':'Observed unchanged state','unresolved':['Required change not established']})])],
        send_command_fn=send,captured=captured,max_iterations=20)
    assert len(sent)==4 and len(captured)==5
    assert result['commands'][-1]['result']['unresolved']

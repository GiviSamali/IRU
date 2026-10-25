"""Unknown execution is read back, never blindly retried (chat 104 regression)."""
import asyncio
import json

import pytest

from server import database as db, controller_onboarding as onboarding, controller
from server import orchestrator as orch
from server.response_presentation import worker_presentation
from server.task_summary import recovered_execution_summary
from server.worker_reports import build_worker_report
from test_orchestrator_worker import owners, task
from test_diagnostic_task_followup import store, cmd, decide_as


def saved_task(owner):
    t=task(owner);t.update(status='unknown',device_ids=[],answer='Не удалось получить корректный ответ от ИИ.')
    fact_id=db.add_user_fact(str(owner['id']),'Проект доступен только для чтения.')
    t['commands']=[{'tool_name':'remember_fact','step_id':f'step_{i}',
        'status':'success' if i==3 else 'failed',
        'result':{'status':'ok','fact_id':fact_id} if i==3 else {'error':'invalid arguments'}} for i in range(1,4)]
    t['worker_report']=build_worker_report(t)
    return t,fact_id


def test_recorded_write_survives_missing_terminal_without_goal_success(owners):
    t,fact_id=saved_task(owners[0])
    view=worker_presentation(t)
    assert f'Факт №{fact_id}' in view['conversational_response']
    assert 'присутствует в памяти' in view['conversational_response']
    assert 'Итог всего поручения не подтверждён' in view['conversational_response']
    assert build_worker_report(t)['status']=='unknown'


def test_deleted_fact_and_other_owner_are_not_reported_as_present(owners):
    t,fact_id=saved_task(owners[0])
    assert 'не найдена' in recovered_execution_summary(t,user_id=owners[1]['id'])
    db.delete_user_fact(str(owners[0]['id']),fact_id)
    assert 'не найдена' in recovered_execution_summary(t)


def test_rc_zero_never_proves_goal(owners):
    t=task(owners[0]);t['commands']=[{'tool_name':'execute_cmd','step_id':'step_1',
        'status':'success','result':{'returncode':0,'stdout':'OK'}}]
    assert 'не подтверждает выполнение всего поручения' in recovered_execution_summary(t)


def test_unknown_replacement_reads_without_submitting(owners,monkeypatch):
    a=owners[0];t,fact_id=saved_task(a);store(t)
    decide_as(monkeypatch,orch.Decision(intent='delegate',scope='server',objective='Повтори',
        assignments=[orch.Assignment(key='retry',objective='Повтори',replaces_source_task_id=t['task_id'])]))
    async def forbidden(*args,**kwargs):pytest.fail('must not submit a replacement')
    result=asyncio.run(orch.run_turn(cmd('Еще раз попробуй'),a,a['chat_id'],forbidden))
    assert not result['worker_task_id']
    assert f'Факт №{fact_id}' in result['answer']
    with db.get_db() as c:
        assert c.execute('SELECT COUNT(*) FROM worker_jobs').fetchone()[0]==1


def test_validation_diagnostic_survives_and_does_not_store_candidate(owners,monkeypatch):
    async def complete(**kwargs):
        return {'choices':[{'message':{'tool_calls':[{'id':'x','function':{
            'name':'answer_text','arguments':json.dumps({'text':'PRIVATE CANDIDATE'})}}]}}]}
    monkeypatch.setattr(controller,'_chat_completion_request',complete)
    from server.run_journal import diagnostic_context_for_task
    state={}
    with diagnostic_context_for_task('validation-regression',state):
        result=asyncio.run(onboarding.process_onboarding_message('Проверь',
            usage_context={'worker_execution':True,'user_id':owners[0]['id']},
            load_llm_config_fn=lambda:{'model':'test'},current_datetime_msk_fn=lambda:'now'))
    diagnostics=[e['validation'] for e in state['diagnostic_trace'] if 'validation' in e]
    assert len(diagnostics)==5
    assert all(d['condition']=='answer_text missing required fields' for d in diagnostics)
    assert all('basis' in d['fields'] for d in diagnostics)
    assert 'PRIVATE' not in json.dumps(diagnostics)
    assert not result['commands']
    assert build_worker_report({**task(owners[0]),'status':'done',**result})['status']=='unknown'

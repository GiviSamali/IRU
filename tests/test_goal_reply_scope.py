"""Narration subjects follow durable request membership, not language heuristics."""
import asyncio
import copy
import json
import time

import pytest

from server import database as db, goal_completion as completion, orchestrator
from server.goal_presentation import goal_presentation
from test_orchestrator_worker import owners


def setup_goal(owner):
    orchestrator.init_turn_storage()
    g = {'goal_id': 'scope-goal', 'revision': 3, 'owner_user_id': owner['id'],
         'chat_id': owner['chat_id'], 'original_request': 'What is on Desktop?',
         'status': 'success', 'terminal': True, 'goal_completed': True, 'tasks': []}
    mids = {}
    with db.get_db() as c:
        c.execute('INSERT INTO worker_goals VALUES(?,?,?,?,?,?)',
                  (g['goal_id'], owner['id'], owner['chat_id'], g['original_request'], 3, time.time()))
        for index, (key, request) in enumerate([
                ('desktop', 'What is on Desktop?'), ('work', 'What is inside Work?'),
                ('summary', 'Summarize all our work')]):
            c.execute('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',
                      (owner['chat_id'], 'user', request, time.time()))
            mid = c.execute('INSERT INTO messages(chat_id,role,content,task_metadata,created_at) VALUES(?,?,?,?,?)',
                (owner['chat_id'], 'assistant', '', json.dumps({'goalId': g['goal_id'], 'resultFirst': True,
                    'taskKind': 'orchestrator', '_taskId': 'turn-' + key}), time.time())).lastrowid
            mids[key] = mid
            c.execute('INSERT INTO orchestrator_turns VALUES(?,?,?,?,?,?,?,?)',
                (owner['id'], key, 'f', 'turn-' + key, owner['chat_id'], mid, '{}', time.time()))
            if key == 'summary':
                continue
            payload = {'original_request': request}
            c.execute('INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                (key, owner['id'], owner['chat_id'], 'success', json.dumps(payload), None, None, index, index, None))
            c.execute('INSERT INTO worker_goal_tasks VALUES(?,?,?)', (key, g['goal_id'], 1))
            c.execute('INSERT INTO worker_goal_request_tasks VALUES(?,?,?)', (owner['id'], 'turn:' + key, key))
            g['tasks'].append({'task_id': key, 'status': 'success', 'objective': request,
                'result': {'result_id': key}, 'caveats': [key + ' limit'],
                'report': {'status': 'success', 'goal_completed': True, 'summary': key + ' contents',
                           'evidence_refs': ['step_1'], 'caveats': [key + ' limit'], 'artifacts': []}})
    return g, mids


def test_material_selects_current_tasks_and_keeps_previous_results_as_context(owners):
    owner, _ = owners
    g, _ = setup_goal(owner)
    before = copy.deepcopy(g)
    for key, expected in [('desktop', ['desktop']), ('work', ['work']), ('summary', ['desktop', 'work'])]:
        scoped = completion._reply_goal(g, owner['id'], owner['chat_id'], 'turn-' + key)
        material = completion._narration_material(scoped, goal_presentation(scoped))
        assert [m['summary'] for m in material['worker_outcomes']] == [v + ' contents' for v in expected]
        assert [m['caveats'] for m in material['worker_outcomes']] == [[v + ' limit'] for v in expected]
        if key == 'work':
            assert material['current_request'] == 'What is inside Work?'
            assert material['answer_scope'] == 'current_turn'
            assert material['goal_context']['previous_outcomes'][0]['summary'] == 'desktop contents'
        if key == 'summary':
            assert material['answer_scope'] == 'whole_goal'
            assert material['current_request'] == 'Summarize all our work'
    assert g == before


def test_same_revision_cache_persistence_and_limits_are_scoped_to_origin(owners, monkeypatch):
    owner, _ = owners
    g, mids = setup_goal(owner)
    calls = []
    monkeypatch.setattr(completion, 'load_llm_config', lambda: {'model': 'fake'})

    async def provider(*args, **kwargs):
        body = json.loads(args[3][-1]['content'])['untrusted_evidence']
        calls.append(body)
        text = '; '.join(item['summary'] for item in body['worker_outcomes'])
        text += '. Limits: ' + '; '.join(v for item in body['worker_outcomes'] for v in item['caveats'])
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'function': {
            'name': 'goal_completion_reply', 'arguments': json.dumps({'answer': text, 'spoken_response': 'Результат готов.'})}}]}}]}

    monkeypatch.setattr(completion, '_chat_completion_request', provider)

    async def scenario():
        replies = {}
        for key in ['desktop', 'work', 'summary', 'work']:
            reply = await completion.present_completed_goal(g, goal_presentation(g), owner['id'], owner['chat_id'],
                origin_task_id='turn-' + key)
            if key in replies:
                assert reply['message'] == replies[key]['message']
                assert reply['reply_request_llm_calls'] == 0
            replies[key] = reply
        assert len(calls) == 3
        assert 'desktop contents' not in replies['work']['message']
        assert replies['work']['caveats'] == ['work limit']
        assert 'Существенные ограничения:' not in replies['work']['message']
        assert 'desktop contents' in replies['summary']['message'] and 'work contents' in replies['summary']['message']
        with db.get_db() as c:
            for key, mid in mids.items():
                assert c.execute('SELECT content FROM messages WHERE id=?', (mid,)).fetchone()[0] == replies[key]['message']
    asyncio.run(scenario())


def test_fallback_adds_only_current_limits(owners, monkeypatch):
    owner, _ = owners
    g, _ = setup_goal(owner)

    async def fail(*args):
        raise TimeoutError('synthetic')

    monkeypatch.setattr(completion, '_narrate', fail)
    reply = asyncio.run(completion.present_completed_goal(g, goal_presentation(g), owner['id'], owner['chat_id'],
        origin_task_id='turn-work'))
    assert 'work limit' in reply['message'] and 'desktop limit' not in reply['message']


def test_legacy_cache_migration_preserves_old_reply_without_reusing_it(owners):
    owner, _ = owners
    with db.get_db() as c:
        c.execute('CREATE TABLE goal_completion_replies (goal_id TEXT,revision INTEGER,owner_user_id INTEGER,'
            'chat_id INTEGER,fingerprint TEXT,answer TEXT,spoken TEXT,created_at REAL,PRIMARY KEY(goal_id,revision))')
        c.execute('INSERT INTO goal_completion_replies VALUES(?,?,?,?,?,?,?,?)',
            ('g', 1, owner['id'], owner['chat_id'], 'f', 'old answer', 'old speech', 1))
        completion._prepare_storage(c)
        row = c.execute('SELECT answer,reply_scope FROM goal_completion_replies').fetchone()
        assert tuple(row) == ('old answer', 'legacy')
        completion._prepare_storage(c)


def partial_goal(owner):
    g, mids = setup_goal(owner)
    g.update(status='partial', goal_completed=False)
    desktop = g['tasks'][0]
    desktop.update(status='failed', result=None)
    desktop['report'].update(status='failed', goal_completed=False, summary='Desktop could not be read')
    return g, mids


def test_successful_current_result_is_distinct_from_partial_goal_in_narration(owners):
    owner, _ = owners
    g, _ = partial_goal(owner)
    before = copy.deepcopy(g)
    scoped = completion._reply_goal(g, owner['id'], owner['chat_id'], 'turn-work')
    material = completion._narration_material(scoped, goal_presentation(scoped))
    assert material['verified_goal_status'] == 'partial'
    assert [m['status'] for m in material['worker_outcomes']] == ['success']
    # The narrator can state success of Work without claiming Goal success.
    assert material.get('verified_current_result_status') == 'success'
    assert g == before


@pytest.mark.parametrize('desktop_failed', [False, True])
def test_desktop_work_summary_chat_reload_and_voice_keep_reply_scope(owners, monkeypatch, desktop_failed):
    from server import voice, worker_scheduler
    from server.routers import chats as chat_routes, voice as voice_routes
    from server.runtime_state import tasks

    owner, _ = owners
    g, _ = partial_goal(owner) if desktop_failed else setup_goal(owner)
    before = copy.deepcopy(g)
    calls, spoken = [], []
    monkeypatch.setattr(completion, 'load_llm_config', lambda: {'model': 'fake'})
    monkeypatch.setattr(worker_scheduler, 'goal_view', lambda *args: copy.deepcopy(g))
    monkeypatch.setattr(chat_routes, 'get_current_user', lambda _: owner)
    monkeypatch.setattr(voice_routes, 'get_current_user', lambda _: owner)
    monkeypatch.setattr(voice_routes, 'check_rate_limit', lambda _: True)
    monkeypatch.setattr(voice, 'speech_configured', lambda: True)

    async def provider(*args, **kwargs):
        material = json.loads(args[3][-1]['content'])['untrusted_evidence']
        calls.append(material)
        subjects = [m['objective'] for m in material['worker_outcomes']]
        if subjects == ['What is inside Work?']:
            text = 'Готово. Папку Работа прочитал: 28 объектов.'
        elif len(subjects) == 2:
            text = 'Desktop прочитать не удалось. Папку Работа прочитал.' if desktop_failed else 'Desktop и папка Работа прочитаны.'
        else:
            text = 'Desktop прочитать не удалось.' if desktop_failed else 'Desktop прочитан.'
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'function': {
            'name': 'goal_completion_reply', 'arguments': json.dumps({'answer': text, 'spoken_response': text})}}]}}]}

    async def synthesize(text):
        spoken.append(text)
        return b'fake-audio'

    async def forbidden(*args, **kwargs):
        raise AssertionError('No additional speech LLM')

    monkeypatch.setattr(completion, '_chat_completion_request', provider)
    monkeypatch.setattr(voice, 'synthesize', synthesize)
    monkeypatch.setattr(voice, 'shorten_answer', forbidden)
    monkeypatch.setitem(tasks, 'turn-work', {'task_id': 'turn-work', 'user_id': owner['id'],
        'chat_id': owner['chat_id'], 'kind': 'orchestrator', 'result_first': True,
        'goal_id': g['goal_id'], 'status': 'done', 'message': 'What is inside Work?', 'answer': ''})

    async def scenario():
        first = await chat_routes.api_get_messages(owner['chat_id'], None)
        again = await chat_routes.api_get_messages(owner['chat_id'], None)
        for reply in (first, again):
            messages = {m['_taskId']: m for m in reply['messages'] if m.get('_taskId')}
            assert messages['turn-work']['content'] == 'Готово. Папку Работа прочитал: 28 объектов.'
            assert 'Desktop' in messages['turn-summary']['content']
            assert 'Работа' in messages['turn-summary']['content']
        response = await voice_routes.task_speech('turn-work', None, part=0)
        assert response.status_code == 200
        monkeypatch.delitem(tasks, 'turn-work')
        restored = await voice_routes.task_speech('turn-work', None, part=0)
        assert restored.status_code == 200
        assert spoken == ['Готово. Папку Работа прочитал: 28 объектов.'] * 2
        assert len(calls) == 3  # Reload and voice reuse the three scoped replies.
        work = next(m for m in calls if m['current_request'] == 'What is inside Work?')
        assert [m['caveats'] for m in work['worker_outcomes']] == [['work limit']]
        assert work['verified_goal_status'] == ('partial' if desktop_failed else 'success')
        if desktop_failed:
            assert work.get('verified_current_result_status') == 'success'
        assert g == before
    asyncio.run(scenario())

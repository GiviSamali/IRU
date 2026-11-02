"""Narration subjects follow durable request membership, not language heuristics."""
import asyncio
import copy
import json
import time

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
                (owner['chat_id'], 'assistant', '', json.dumps({'goalId': g['goal_id'], 'resultFirst': True}), time.time())).lastrowid
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

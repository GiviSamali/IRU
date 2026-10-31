"""FF-02: supplied LIVE requests, synthetic reports, no device/provider access."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from server import database as db, goal_completion as completion, worker_scheduler
from server.goal_presentation import goal_presentation
from server.routers import chats
from test_orchestrator_worker import owners
from test_v3_goal_coverage_and_speech import origin


REQUESTS = [
    ('Создай alpha.txt и beta.txt.', 'Создал alpha.txt и beta.txt с указанным содержимым. Оба файла готовы.'),
    ('Открой папку IRU-EC01-LIVE', 'Открыл папку IRU-EC01-LIVE в Проводнике.'),
    ('Замени 2026 на 2024 в mixed.txt.', 'Заменил три вхождения 2026 на 2024. Остальные данные файла сохранил.'),
]


def fixture(owner, index=0, status='success'):
    names = ['alpha.txt', 'beta.txt'] if index == 0 else ([] if index == 1 else ['mixed.txt'])
    members = []
    for i, name in enumerate(names or ['folder']):
        tid = f'worker-{i}'
        files = [] if index == 1 else [{'path': 'C:\\LIVE\\' + name, 'name': name,
            'device_id': 'pc', 'source_task_id': tid, 'verified': True,
            'size_bytes': 20, 'sha256': 'a' * 64}]
        fact = (f'Создан {name} с указанным содержимым.' if index == 0 else
                'Запрошенное состояние окна подтверждено. Папка IRU-EC01-LIVE открыта в Проводнике.' if index == 1 else
                'В mixed.txt заменены три вхождения 2026 на 2024. Незатронутые байты сохранены.')
        members.append({'task_id': tid, 'status': status, 'required_for_goal': True,
            'execution_scope': 'window' if index == 1 else 'file', 'objective': 'Создать ' + name,
            'result': {'sha256': 'frozen'}, 'report': {'status': status,
                'goal_completed': status == 'success', 'artifacts': files, 'evidence_refs': ['step_1'],
                'summary': fact + '\n' + ('SHA-256 ' + 'a'*64 +
                    ' C:\\LIVE\\' + name + ' 20 bytes BOM CRLF\n') * 20}})
    return {'goal_id': 'ff02', 'revision': 1, 'status': status, 'terminal': True,
        'owner_user_id': owner['id'], 'chat_id': owner['chat_id'],
        'goal_completed': status == 'success', 'original_request': REQUESTS[index][0], 'tasks': members}


def provider(monkeypatch, answer, mode='normal'):
    calls = []
    monkeypatch.setattr(completion, 'load_llm_config', lambda: {'model': 'mock'})
    async def fake(*args, **kwargs):
        calls.append(kwargs)
        if mode == 'error':
            raise RuntimeError('provider unavailable')
        if mode == 'timeout':
            raise asyncio.TimeoutError()
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'function': {'name': 'goal_completion_reply', 'arguments': json.dumps({
                'answer': answer, 'spoken_response': 'Результаты готовы; выводы сохранил в чате.'})}}]}}]}
    monkeypatch.setattr(completion, '_chat_completion_request', fake)
    return calls


@pytest.mark.parametrize('index', range(3))
def test_live_request_projection_and_normal_answer(owners, monkeypatch, index):
    owner, _ = owners
    g = fixture(owner, index); before = copy.deepcopy(g)
    origin(owner, g)
    calls = provider(monkeypatch, REQUESTS[index][1])
    projected = goal_presentation(g)
    assert 'SHA-256' not in projected['message'] and 'C:\\' not in projected['message']
    reply = asyncio.run(completion.present_completed_goal(g, projected, owner['id'], owner['chat_id']))
    assert reply['message'] == REQUESTS[index][1]
    assert len(reply['result_artifacts']) == (2 if index == 0 else 0 if index == 1 else 1)
    assert len(calls) == (0 if index == 1 else 1)
    assert g == before  # No WorkerReport, status or evidence mutation.


@pytest.mark.parametrize('mode', ['normal', 'error', 'timeout', 'narrator_timeout'])
def test_poll_history_and_database_restore_share_one_answer(owners, monkeypatch, mode):
    owner, _ = owners; g = fixture(owner); mid = origin(owner, g)
    # History identifies the originating turn using existing metadata.
    with db.get_db() as c:
        row = c.execute('SELECT task_metadata FROM messages WHERE id=?', (mid,)).fetchone()
        metadata = json.loads(row['task_metadata']); metadata['taskKind'] = 'orchestrator'
        c.execute('UPDATE messages SET task_metadata=? WHERE id=?', (json.dumps(metadata), mid))
    calls = provider(monkeypatch, REQUESTS[0][1], mode)
    if mode == 'narrator_timeout':
        async def timed_out(*args):
            calls.append('narrator')
            raise asyncio.TimeoutError()
        monkeypatch.setattr(completion, '_narrate', timed_out)
    monkeypatch.setattr(chats, 'get_current_user', lambda _: owner)
    monkeypatch.setattr(worker_scheduler, 'goal_view', lambda *args: copy.deepcopy(g))
    async def scenario():
        projected = goal_presentation(g)
        first, repeated = await asyncio.gather(*[
            completion.present_completed_goal(g, projected, owner['id'], owner['chat_id']) for _ in range(2)])
        history = await chats.api_get_messages(owner['chat_id'], SimpleNamespace())
        message = next(m for m in history['messages'] if m.get('goalId') == g['goal_id'])
        assert message['content'] == message['conversationalResponse'] == first['message'] == repeated['message']
        assert message['goalPresentation']['message'] == first['message']
        completion._LOCKS.clear()  # Fresh runtime, persisted SQLite reply remains authoritative.
        restored = await completion.present_completed_goal(g, projected, owner['id'], owner['chat_id'])
        assert restored['message'] == first['message'] and len(calls) == 1
        assert 'SHA-256' not in first['message'] and len(first['message']) < 400
        with db.get_db() as c:
            assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
            assert c.execute('SELECT content FROM messages WHERE id=?', (mid,)).fetchone()[0] == first['message']
    asyncio.run(scenario())


def test_history_can_finish_unpolled_goal_once_and_old_cache_never_echoes_reports(owners, monkeypatch):
    owner, _ = owners; g = fixture(owner); mid = origin(owner, g)
    with db.get_db() as c:
        row = c.execute('SELECT task_metadata FROM messages WHERE id=?', (mid,)).fetchone()
        metadata = json.loads(row[0]); metadata['taskKind'] = 'orchestrator'
        c.execute('UPDATE messages SET task_metadata=? WHERE id=?', (json.dumps(metadata), mid))
    monkeypatch.setattr(chats, 'get_current_user', lambda _: owner)
    monkeypatch.setattr(worker_scheduler, 'goal_view', lambda *args: g)
    calls = provider(monkeypatch, REQUESTS[0][1])
    async def scenario():
        for _ in range(2):
            history = await chats.api_get_messages(owner['chat_id'], None)
            assert history['messages'][0]['content'] == REQUESTS[0][1]
        assert len(calls) == 1
        with db.get_db() as c:
            c.execute('UPDATE goal_completion_replies SET fingerprint=?,answer=?',
                      ('pre-ff02', g['tasks'][0]['report']['summary']))
        history = await chats.api_get_messages(owner['chat_id'], None)
        assert 'SHA-256' not in history['messages'][0]['content'] and len(calls) == 1
    asyncio.run(scenario())


def test_partial_fallback_identifies_available_and_missing_results(owners, monkeypatch):
    owner, _ = owners; g = fixture(owner, status='partial')
    g['tasks'][0]['status'] = 'success'; g['tasks'][0]['report']['goal_completed'] = True
    g['tasks'][1]['report']['artifacts'] = []
    reply = goal_presentation(g)
    assert not reply['goal_completed'] and reply['status'] == 'partial'
    assert 'alpha.txt' in reply['message'] and 'Не завершено: Создать beta.txt' in reply['message']
    assert len(reply['result_artifacts']) == 1 and 'Готово' not in reply['message']


def test_research_keeps_long_findings_and_requested_technical_details(owners, monkeypatch):
    owner, _ = owners; g = fixture(owner)
    origin(owner, g)
    g['original_request'] = 'Исследуй проблемы и предоставь подробный технический отчёт с SHA-256.'
    answer = '\n\n'.join(f'{i}. Вывод с причиной, ограничениями и рекомендациями.' for i in range(1, 90))
    answer += '\nSHA-256: ' + 'a' * 64
    for member in g['tasks']: member['report']['summary'] = answer
    calls = provider(monkeypatch, answer)
    reply = asyncio.run(completion.present_completed_goal(g, goal_presentation(g), owner['id'], owner['chat_id']))
    assert len(answer) > 2000 and reply['message'] == answer, reply.get('reply_metrics')
    assert calls[0]['max_tokens'] > 800 and len(reply['spoken_message']) <= 420


@pytest.mark.parametrize('status', ['running', 'queued', 'waiting_confirmation', 'waiting_input', 'failed', 'unknown', 'cancelled', 'blocked'])
def test_status_and_attention_are_not_changed_by_presentation(owners, status):
    owner, _ = owners; g = fixture(owner, status=status)
    g['terminal'] = status in {'failed', 'unknown', 'cancelled', 'blocked'}
    projected = goal_presentation(g)
    assert projected['status'] == status and projected['terminal'] == g['terminal']
    assert projected['requires_user_action'] == (status in {'waiting_confirmation', 'waiting_input'})
    assert not projected['goal_completed']

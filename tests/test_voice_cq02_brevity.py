"""Prompt delivery and routing contracts, not real-model style evaluation."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from server import controller, database as db, orchestrator as orch, voice
from server.runtime_state import tasks
from test_voice_cq02_no_semantics import REPLIES


CASES = [
    ('Как там мой компьютер?', 'Компьютер givi на связи. Подробное состояние пока не проверено.', 'Компьютер givi на связи. Подробное состояние пока не проверено.'),
    ('Что думаешь об одном непрерывном разговоре?', 'Я за непрерывность смысла. Она помогает сохранять решения, но требует различать действующее и отменённое.', 'Я за непрерывность смысла, если память различает действующие и отменённые решения.'),
    ('Назови два недостатка своей идеи', '1. Сжатие теряет оговорки.\n2. Записи памяти могут устаревать.', 'Два недостатка: потеря оговорок при сжатии и устаревание записей памяти.'),
    ('А второй подробнее', 'Второй недостаток — устаревание записей памяти. ' + 'Память должна сохранять источник и время проверки; иначе старое решение выглядит действующим. '*8, 'Записи памяти устаревают. Поэтому нужны источник и время проверки, иначе старое решение выглядит действующим.'),
    ('Объясни подробно память и сжатие', 'История хранит исходные реплики, рабочая память — актуальное состояние. ' + 'Сжатие теряет оговорки и основания решений; связи с источником позволяют проверить спорную запись. '*9, 'История хранит исходные слова, память — рабочее состояние. При сжатии теряются оговорки, поэтому спорные записи нужно сверять с источником.'),
    ('В двух предложениях подведи итог', 'Непрерывность требует актуальной памяти. История нужна для проверки спорных записей.', 'Непрерывность требует актуальной памяти. История нужна для проверки спорных записей.'),
    ('Файл создан, но содержимое не проверено', 'Создание подтверждено. Содержимое ещё не проверено.', 'Файл создан, содержимое ещё не проверено.'),
]


@pytest.mark.parametrize('message,answer,speech', [(m, a.strip(), s) for m, a, s in CASES], ids=['pc','opinion','critique','second_detail','deep_analysis','two_sentences','unverified_content'])
def test_generation_rules_reach_primary_call_and_tts_without_extra_llm(client, monkeypatch, message, answer, speech):
    user = db.create_user('brevity'); chat = db.create_chat(user['id'], 'brevity')['id']
    previous = 'Первый недостаток — потеря оговорок. Второй — устаревание записей памяти.'
    db.add_message(chat, 'assistant', previous)
    calls = []; spoken = []
    async def completion(*args, **kwargs):
        messages = args[3]
        prompt = messages[0]['content']
        assert '150–250' in prompt and '420 — технический максимум' in prompt
        assert '1–2 коротких предложений' in prompt and '1–2 главных аргументов' in prompt
        assert 'двух недостатков, без лекции' in prompt
        assert 'фактическую краткость' in prompt and 'именно второй пункт' in prompt
        assert 'не сокращай нужный анализ искусственно' in prompt
        assert 'не добавляй автоматически новые поручения' in prompt
        assert 'созданным файлом и непроверенным содержимым' in prompt
        context = json.loads(messages[1]['content'])['untrusted_context']
        assert any(row['content'] == previous for row in context['history'])
        assert messages[-1]['content'] == message
        calls.append(kwargs)
        return {'choices': [{'finish_reason': 'stop', 'message': {'tool_calls': [{'function': {
            'name': 'orchestrator_decision', 'arguments': json.dumps({'intent': 'conversation', 'answer': answer, 'spoken_response': speech})}}]}}]}
    async def forbidden(*args, **kwargs):
        raise AssertionError('No Worker or additional editorial call for accepted primary speech')
    async def synthesize(text):
        spoken.append(text); return b'mock-ogg'
    monkeypatch.setattr(orch, '_chat_completion_request', completion)
    monkeypatch.setattr(orch, 'load_llm_config', lambda: {'model': 'mock'})
    monkeypatch.setattr(voice, 'shorten_answer', forbidden)
    monkeypatch.setattr(voice, 'synthesize', synthesize)
    monkeypatch.setenv('YANDEX_API_KEY', 'mock')
    cmd = SimpleNamespace(message=message, request_id='brevity', device_id='', modes={}, broadcast=False)
    tid = asyncio.run(orch.run_turn(cmd, user, chat, forbidden))['task_id']
    response = client.post(f'/api/voice/tasks/{tid}/speech', headers={'X-Token': user['token']})
    assert response.status_code == 200 and spoken == [speech]
    assert len(calls) == 1 and tasks[tid]['answer'] == answer
    assert tasks[tid]['dialogue_intent'] == 'conversation'


@pytest.mark.parametrize('message_id', [2936, 2940, 2942])
def test_editor_prompt_on_actual_overlong_cases_with_new_mock_output(monkeypatch, message_id):
    reply = next(r for r in REPLIES if r['message_id'] == message_id)
    calls = []
    # New controlled provider output, not a claim of new LLM generation.
    speech = 'Рабочая память помогает продолжать разговор, но может устаревать и терять оговорки. Поэтому спорные записи нужно сверять с исходными словами и текущими данными.'
    async def completion(**kwargs):
        prompt = kwargs['messages'][0]['content']
        assert '150–250' in prompt and '420 символов — технический максимум' in prompt
        assert 'главный вывод и важнейшую оговорку' in prompt
        assert 'не перечисляй все пункты' in prompt
        assert kwargs['max_tokens'] == 250 and kwargs['phase'] == 'voice_brief'
        assert json.loads(kwargs['messages'][1]['content'])['answer'] == reply['answer']
        calls.append(kwargs)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': speech}}]}
    monkeypatch.setattr(controller, 'load_llm_config', lambda: {'model': 'deepseek-v4-flash'})
    monkeypatch.setattr(controller, '_chat_completion_request', completion)
    task = {'kind': 'orchestrator', 'status': 'done', 'dialogue_intent': 'conversation',
            'answer': reply['answer'], 'message': reply['request'],
            'dialogue_spoken_response': reply['primary'], 'dialogue_speech_answer': reply['answer']}
    assert asyncio.run(voice.spoken_parts(task)) == [speech]
    assert len(calls) == 1 and task['answer'] == reply['answer']
    assert task['speech_diagnostics']['selection']['mode'] == 'editorial'
    assert voice.VOICE_BRIEF_MAX_COST_USD == .005

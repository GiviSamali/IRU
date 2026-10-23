"""Replay captured provider output from chat 102, without paid calls."""
import asyncio
import json
from pathlib import Path

import pytest

from server import controller, voice

REPLIES = json.loads((Path(__file__).parent / 'fixtures/voice_chat102_2931_2950.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('reply', REPLIES, ids=lambda r: str(r['message_id']))
def test_actual_primary_and_editorial_candidates(reply, monkeypatch):
    calls = []
    async def completion(**kwargs):
        calls.append(kwargs)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': reply['editorial']}}]}
    monkeypatch.setattr(controller, 'load_llm_config', lambda: {'model': 'deepseek-v4-flash'})
    monkeypatch.setattr(controller, '_chat_completion_request', completion)
    task = {'kind': 'orchestrator', 'status': 'done', 'dialogue_intent': 'conversation',
            'answer': reply['answer'], 'message': reply['request'],
            'dialogue_spoken_response': reply['primary'], 'dialogue_speech_answer': reply['answer']}
    parts = asyncio.run(voice.spoken_parts(task))
    primary_ok = len(reply['primary']) <= 420
    editorial_ok = isinstance(reply['editorial'], str) and len(reply['editorial']) <= 420
    expected = reply['primary'] if primary_ok else reply['editorial'] if editorial_ok else voice.SPEECH_UNAVAILABLE
    assert ' '.join(parts) == expected
    assert task['answer'] == reply['answer']
    assert len(calls) == (0 if primary_ok else 1)
    assert task['speech_diagnostics']['selection']['editorial_llm_calls'] == len(calls)
    assert task['speech_diagnostics']['selection']['mode'] == ('primary' if primary_ok else 'editorial' if editorial_ok else 'unavailable')
    assert len(expected) <= 420
    assert asyncio.run(voice.spoken_parts(task)) == parts
    assert len(calls) == (0 if primary_ok else 1)


@pytest.mark.parametrize('word', ['возможно', 'не', 'частично', 'отменено'])
@pytest.mark.parametrize('informational', [True, False])
def test_words_never_control_technical_acceptance(word, informational):
    check = {}
    speech = f'{word}. Готово, файл удалён, порт 9090.'
    assert voice.conversational_speech(speech, 'Порт 8080. Файл не удалён.', informational=informational, diagnostic=check) == speech
    assert check == {'reason': 'accepted'}


@pytest.mark.parametrize('kind', ['clarification', 'input'])
def test_long_structured_question_is_not_read_in_full(kind, monkeypatch):
    answer = 'Уточни параметры задачи. ' * 40
    task = {'answer': answer, 'status': 'waiting_input', 'input_required': {'presented_by': 'orchestrator'}}
    if kind == 'clarification':
        from server import response_presentation
        task.update(worker_id='worker', status='blocked')
        monkeypatch.setattr(response_presentation, 'worker_presentation', lambda *args: {'conversational_response': answer})
        monkeypatch.setattr(response_presentation, 'worker_spoken_response', lambda *args: answer)
        monkeypatch.setattr(response_presentation, 'normalized_worker_report', lambda *args: {'error_code': 'clarification_required'})
    calls = []
    async def brief(t):
        calls.append(t)
        return 'Какие параметры использовать?'
    monkeypatch.setattr(voice, 'shorten_answer', brief)
    assert asyncio.run(voice.spoken_parts(task)) == ['Какие параметры использовать?']
    assert len(calls) == 1 and task['answer'] == answer


def test_restored_candidate_is_revalidated_technically_not_by_historical_semantic_reason(monkeypatch):
    from hashlib import sha256
    answer = 'Исходный полный ответ. ' * 40
    speech = 'Возможно, частично выполнено, но не проверено.'
    task = {'answer': answer, 'speech_diagnostics': {
        'editorial': {'candidate': speech, 'reason': 'outcome_mismatch', 'answer_sha256': sha256(answer.encode()).hexdigest()}}}
    async def forbidden(*args):
        raise AssertionError('Stored TTS-compatible candidate needs no additional call')
    monkeypatch.setattr(voice, 'shorten_answer', forbidden)
    assert asyncio.run(voice.spoken_parts(task)) == [speech]
    assert task['speech_diagnostics']['selection']['editorial_llm_calls'] == 0
    assert task['speech_diagnostics']['editorial']['reason'] == 'accepted'


@pytest.mark.parametrize('value, reason', [(None, 'invalid_type'), ('', 'missing_candidate'), ('x'*421, 'candidate_too_long'), ('Ответ \ud800', 'invalid_encoding'), ('```code```', 'speech_format')])
def test_only_technical_rejection_reasons(value, reason):
    diagnostic = {}
    assert voice.conversational_speech(value, diagnostic=diagnostic) == ''
    assert diagnostic == {'reason': reason}

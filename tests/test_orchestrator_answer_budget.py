import pytest
from pydantic import ValidationError
from server.orchestrator import Decision, MAX_ANSWER_CHARS, TOOL


@pytest.mark.parametrize('length', [2001, 6000])
def test_conversation_preserves_full_answer(length):
    text = 'я' * length
    decision = Decision.model_validate({'intent': 'conversation', 'answer': text})
    assert decision.answer == text


def test_overlong_conversation_is_bounded_without_mutating_input():
    payload = {'intent': 'conversation', 'answer': 'я' * 7000, 'spoken_response': 'Краткий ответ.'}
    decision = Decision.model_validate(payload)
    assert len(decision.answer) == MAX_ANSWER_CHARS
    assert decision.answer.endswith('[Ответ сокращён.]')
    assert decision.spoken_response == payload['spoken_response']
    assert len(payload['answer']) == 7000
    assert TOOL['function']['parameters']['properties']['answer']['maxLength'] == MAX_ANSWER_CHARS


@pytest.mark.parametrize('intent', ['delegate', 'cancel', 'task_status', 'clarify'])
def test_other_intents_reject_overlong_answer(intent):
    with pytest.raises(ValidationError):
        Decision.model_validate({'intent': intent, 'answer': 'я' * 7000})


@pytest.mark.parametrize('critical', [
    {'scope': None}, {'execution_mode': 'unapproved'},
    {'target_device_ids': [1]}, {'verified': True}, {'objective': 'x' * 2001},
])
def test_shortening_never_bypasses_critical_validation(critical):
    with pytest.raises(ValidationError):
        Decision.model_validate({'intent': 'conversation', 'answer': 'я' * 7000, **critical})


def test_answer_type_remains_strict():
    with pytest.raises(ValidationError):
        Decision.model_validate({'intent': 'conversation', 'answer': 123})
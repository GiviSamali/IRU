import asyncio

import pytest

from server import voice


def test_short_conversation_needs_no_extra_llm_call(monkeypatch):
    async def unexpected(task):
        pytest.fail("Short answer should not require LLM")
    monkeypatch.setattr(voice, "shorten_answer", unexpected)
    task = {"answer": "Привет! Чем могу помочь?", "message": "Иру, привет", "status": "done"}
    assert asyncio.run(voice.spoken_parts(task)) == [task["answer"]]


def test_brief_is_cached_without_changing_full_answer(monkeypatch):
    calls = []
    async def shorten(task):
        calls.append(task["answer"])
        return "Файл создан, но загрузить его не удалось."
    monkeypatch.setattr(voice, "shorten_answer", shorten)
    answer = r"Файл создан: C:\work\report.txt. Но загрузить его не удалось."
    task = {"answer": answer, "message": "Создай и загрузи файл", "status": "completed_with_recovery"}
    first = asyncio.run(voice.spoken_parts(task))
    assert asyncio.run(voice.spoken_parts(task)) == first
    assert first == ["Файл создан, но загрузить его не удалось."]
    assert calls == [answer]
    assert task["answer"] == answer
    task["answer"] += " Попробуйте повторить загрузку."
    asyncio.run(voice.spoken_parts(task))
    assert len(calls) == 2


@pytest.mark.parametrize("user_message", ["Расскажи подробно", "Назови полный путь", "Где сохранён файл?"])
def test_explicit_details_preserve_inline_paths(user_message, monkeypatch):
    async def unexpected(task):
        pytest.fail("Explicit details should not be summarized")
    monkeypatch.setattr(voice, "shorten_answer", unexpected)
    task = {"message": user_message, "answer": r"Файл: `C:\work\my_report.txt`", "status": "done"}
    assert asyncio.run(voice.spoken_parts(task)) == [r"Файл: C:\work\my_report.txt"]


@pytest.mark.parametrize("user_message", [r"Создай файл по пути C:\work\report.txt", "Не озвучивай путь", "Не говори подробно"])
def test_technical_input_is_not_a_request_for_verbose_speech(user_message):
    assert not voice.wants_spoken_details(user_message)


@pytest.mark.parametrize("error", [RuntimeError("private"), asyncio.TimeoutError()])
def test_summary_failure_never_reads_a_long_answer_or_claims_success(monkeypatch, error):
    async def fail(task): raise error
    monkeypatch.setattr(voice, "shorten_answer", fail)
    task = {"answer": "Подготовка началась. " * 50 + "Выполнение не удалось.", "status": "error"}
    parts = asyncio.run(voice.spoken_parts(task))
    assert parts == ["Коротко пересказать сейчас не получилось. Полный ответ оставила в чате."]


@pytest.mark.parametrize("content,finish", [
    ("Готово", "length"), ("", "stop"), ("а" * 421, "stop"),
    (r"Файл создан C:\work\report.txt", "stop"),
])
def test_incomplete_or_technical_model_output_is_rejected(monkeypatch, content, finish):
    from server import controller
    monkeypatch.setattr(controller, "load_llm_config", lambda: {"model": "deepseek-v4-flash"})
    async def completion(**kwargs):
        assert "tools" not in kwargs
        assert kwargs["phase"] == "voice_brief"
        return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    monkeypatch.setattr(controller, "_chat_completion_request", completion)
    with pytest.raises(ValueError):
        asyncio.run(voice.shorten_answer({"answer": "Ответ", "status": "done"}))


def test_summary_request_uses_answer_and_ownership_for_usage_not_tool_logs(monkeypatch):
    from server import controller
    monkeypatch.setattr(controller, "load_llm_config", lambda: {"model": "deepseek-v4-flash"})
    async def completion(**kwargs):
        import json
        payload = json.loads(kwargs["messages"][1]["content"])
        assert payload == {"request": "Сделай файл", "status": "done", "answer": "Файл создан, запуск не проверен."}
        assert kwargs["usage_context"]["user_id"] == 7
        assert kwargs["usage_context"]["poll_task_id"] == "test-task"
        assert "tools" not in kwargs
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Файл создан, запуск не проверен."}}]}
    monkeypatch.setattr(controller, "_chat_completion_request", completion)
    task = {"message": "Сделай файл", "answer": "Файл создан, запуск не проверен.", "status": "done",
            "user_id": 7, "task_id": "test-task", "commands": ["private output"]}
    assert asyncio.run(voice.shorten_answer(task)) == task["answer"]


def test_long_primary_is_brief_and_chat_is_unchanged(monkeypatch):
    calls=[]
    async def brief(task):
        calls.append(task['answer']);return 'LAN соединяет устройства рядом, WAN — на большом расстоянии.'
    monkeypatch.setattr(voice,'shorten_answer',brief)
    original='LAN и WAN. '*100
    task={'kind':'orchestrator','answer':original,'message':'Объясни LAN и WAN','status':'done'}
    assert len(asyncio.run(voice.spoken_parts(task))[0])<420
    asyncio.run(voice.spoken_parts(task))
    assert calls==[original] and task['answer']==original
    assert task['spoken_response'].startswith('LAN')


def test_read_file_is_not_full_read_aloud(monkeypatch):
    async def brief(task):return 'В config.ini указан port=8080.'
    monkeypatch.setattr(voice,'shorten_answer',brief)
    task={'kind':'orchestrator','answer':'Содержимое config.ini. '*100,'message':'Прочитай файл config.ini'}
    assert asyncio.run(voice.spoken_parts(task))==['В config.ini указан port=8080.']
    task['message']='Прочитай ответ вслух полностью'
    assert len(' '.join(asyncio.run(voice.spoken_parts(task))))>420


def test_voice_brief_input_budget_prevents_request(monkeypatch):
    from server import controller
    monkeypatch.setattr(controller,'load_llm_config',lambda:{})
    async def forbidden(**kwargs):raise AssertionError('Budget must precede request')
    monkeypatch.setattr(controller,'_chat_completion_request',forbidden)
    with pytest.raises(ValueError,match='budget'):
        asyncio.run(voice.shorten_answer({'answer':'a'*12001}))


@pytest.mark.parametrize('utterance',['Озвучь полностью','Зачитай целиком','Прочитай ответ вслух полностью'])
def test_full_speech_requires_explicit_read_aloud(utterance):
    assert voice.wants_full_speech(utterance)
    assert not voice.wants_full_speech('Прочитай файл полностью и сравни значения')


def test_brief_preserves_inline_values_and_underscored_filename(monkeypatch):
    from server import controller
    monkeypatch.setattr(controller,'load_llm_config',lambda:{'model':'deepseek-v4-flash'})
    async def completion(**kwargs):
        return {'choices':[{'finish_reason':'stop','message':{'content':'В `my_config.ini` указано `port=8080`.'}}]}
    monkeypatch.setattr(controller,'_chat_completion_request',completion)
    assert 'my_config.ini' in asyncio.run(voice.shorten_answer({'answer':'В my_config.ini указано port=8080.'}))


def test_configured_cost_cap_prevents_paid_request(monkeypatch):
    from server import controller
    monkeypatch.setattr(controller,'load_llm_config',lambda:{'model':'deepseek-v4-flash','deepseek_pricing_usd_per_1m':{'deepseek-v4-flash':{'output':1000}}})
    async def forbidden(**kwargs):raise AssertionError('cost limit must precede request')
    monkeypatch.setattr(controller,'_chat_completion_request',forbidden)
    with pytest.raises(ValueError,match='budget'):
        asyncio.run(voice.shorten_answer({'answer':'Ответ'}))

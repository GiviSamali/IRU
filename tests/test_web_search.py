import asyncio
import base64
import json

import httpx
import pytest

from server import web_search

XML = '''<?xml version="1.0" encoding="UTF-8"?>
<yandexsearch><response><results><grouping>
<group><doc><url>https://example.org/one</url><domain>example.org</domain>
<title>Новости <hlword>науки</hlword> &amp; техники</title>
<passages><passage>Первый <hlword>результат</hlword>.</passage><passage>Вторая строка.</passage></passages></doc></group>
<group><doc><url>https://example.net/two</url><title>Второй результат</title><headline>Краткое описание</headline></doc></group>
</grouping></results></response></yandexsearch>'''


def encoded(xml=XML):
    return {'rawData': base64.b64encode(xml.encode('utf-8')).decode('ascii')}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv('YANDEX_SEARCH_API_KEY', 'test-search-secret')
    monkeypatch.setenv('YANDEX_FOLDER_ID', 'test-folder')
    original_client = httpx.AsyncClient
    requests = []
    replies = []
    def handle(request):
        requests.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(web_search.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    async def no_sleep(seconds): pass
    monkeypatch.setattr(web_search.asyncio, 'sleep', no_sleep)
    return requests, replies


def test_success_request_and_multiple_normalized_results(backend):
    requests, replies = backend
    replies.append(httpx.Response(200, json=encoded()))
    result = asyncio.run(web_search.run_web_search(' новости науки ', 2))
    assert result == {'answer': None, 'results': [
        {'title': 'Новости науки & техники', 'url': 'https://example.org/one', 'content': 'Первый результат. Вторая строка.'},
        {'title': 'Второй результат', 'url': 'https://example.net/two', 'content': 'Краткое описание'},
    ]}
    assert str(requests[0].url) == web_search.SEARCH_URL
    assert requests[0].headers['Authorization'] == 'Api-Key test-search-secret'
    body = json.loads(requests[0].content)
    assert body == {'folderId': 'test-folder', 'query': {'queryText': 'новости науки', 'searchType': 'SEARCH_TYPE_RU'},
                    'responseFormat': 'FORMAT_XML', 'maxPassages': '4',
                    'groupSpec': {'groupMode': 'GROUP_MODE_FLAT', 'groupsOnPage': '2', 'docsInGroup': '1'}}
    assert 'test-search-secret' not in requests[0].content.decode()


def test_base64_utf8_highlights_and_limit():
    assert web_search.parse_search_response(encoded(), 1)['results'][0]['title'] == 'Новости науки & техники'
    assert len(web_search.parse_search_response(encoded(), 1)['results']) == 1


def test_missing_fields_domain_fallback_namespace_and_compaction():
    xml = '<yandexsearch xmlns="urn:test"><response><results><doc><domain>example.org</domain><passages><passage>' + 'я' * 1200 + '</passage></passages></doc><doc><title>Без адреса</title></doc></results></response></yandexsearch>'
    results = web_search.parse_search_response(encoded(xml), 10)['results']
    assert results == [{'title': 'example.org', 'url': 'https://example.org', 'content': 'я' * 800}]


@pytest.mark.parametrize('xml', ['<yandexsearch><response><results/></response></yandexsearch>',
                               '<yandexsearch><response><error code="15">Ничего не найдено</error></response></yandexsearch>'])
def test_empty_results_are_success(xml):
    assert web_search.parse_search_response(encoded(xml), 5) == {'answer': None, 'results': []}


@pytest.mark.parametrize('code,attempts', [(401, 1), (403, 1), (429, 1), (500, 2), (502, 2), (503, 2)])
def test_http_errors_are_bounded_and_do_not_leak_provider_body(backend, code, attempts):
    requests, replies = backend
    replies.extend(httpx.Response(code, text='test-search-secret private provider details') for _ in range(attempts))
    result = asyncio.run(web_search.run_web_search('тест'))
    assert f'HTTP {code}' in result['error']
    assert 'test-search-secret' not in str(result)
    assert 'private' not in str(result)
    assert len(requests) == attempts


def test_5xx_then_success(backend):
    requests, replies = backend
    replies.extend([httpx.Response(503), httpx.Response(200, json=encoded())])
    assert len(asyncio.run(web_search.run_web_search('тест'))['results']) == 2
    assert len(requests) == 2


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failures(backend, error):
    requests, replies = backend
    replies.extend([error('test-search-secret'), error('test-search-secret')])
    result = asyncio.run(web_search.run_web_search('тест'))
    assert 'сетевая ошибка' in result['error']
    assert 'test-search-secret' not in str(result)
    assert len(requests) == 2


@pytest.mark.parametrize('response', [
    httpx.Response(200, text='{broken'), httpx.Response(200, json=[]),
    httpx.Response(200, json={}), httpx.Response(200, json={'rawData': 123}),
    httpx.Response(200, json={'rawData': ''}), httpx.Response(200, json={'rawData': '!!!'}),
    httpx.Response(200, json={'rawData': 'YWJj='}),
    httpx.Response(200, json={'rawData': base64.b64encode(b'\xff').decode()}),
    httpx.Response(200, json=encoded('<yandexsearch>')),
    httpx.Response(200, json=encoded('<html/>')),
    httpx.Response(200, json=encoded('<!DOCTYPE yandexsearch [<!ENTITY x "private">]><yandexsearch><response>&x;</response></yandexsearch>')),
    httpx.Response(200, json=encoded('<yandexsearch><response><error code="32">private</error></response></yandexsearch>')),
])
def test_malformed_responses_return_tool_error(backend, response):
    requests, replies = backend
    replies.append(response)
    result = asyncio.run(web_search.run_web_search('тест'))
    assert set(result) == {'error'}
    assert 'private' not in str(result)
    assert len(requests) == 1


@pytest.mark.parametrize('env_name', ['YANDEX_SEARCH_API_KEY', 'YANDEX_FOLDER_ID'])
def test_missing_configuration_never_calls_network(backend, monkeypatch, env_name):
    requests, _ = backend
    monkeypatch.delenv(env_name)
    assert env_name in asyncio.run(web_search.run_web_search('тест'))['error']
    assert requests == []


@pytest.mark.parametrize('query', ['', '   ', 'я' * 401])
def test_invalid_queries_never_call_network(backend, query):
    requests, _ = backend
    assert 'error' in asyncio.run(web_search.run_web_search(query))
    assert requests == []


@pytest.mark.parametrize('limit,expected', [(100, 10), (-2, 1), (None, 5)])
def test_result_count_clamped(backend, limit, expected):
    requests, replies = backend
    replies.append(httpx.Response(200, json=encoded()))
    asyncio.run(web_search.run_web_search('тест', limit))
    assert json.loads(requests[0].content)['groupSpec']['groupsOnPage'] == str(expected)


@pytest.mark.parametrize('pipeline', [False, True])
@pytest.mark.parametrize('status', [200, 403])
def test_both_controller_paths_deliver_search_result_to_llm(client, backend, pipeline, status):
    from server.controller_non_pipeline import process_non_pipeline_command
    from server.controller_pipeline import run_pipeline_worker
    requests, replies = backend
    replies.append(httpx.Response(status, json=encoded()))
    captured = []
    async def completion(**kwargs):
        tool_messages = [m for m in kwargs['messages'] if m.get('role') == 'tool']
        if tool_messages:
            captured.extend(json.loads(m['content']) for m in tool_messages)
            name = 'answer_text'
            args = {'answer_type': 'pure_text', 'basis': [], 'text': 'Результаты поиска получены.', 'self_check': {
                'depends_on_current_external_state': False, 'claims_completed_action': False,
                'has_sufficient_evidence': True, 'missing_evidence_question': ''}}
        else:
            name, args = 'web_search', {'query': 'наука', 'max_results': 2}
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': '', 'tool_calls': [
            {'id': 'call-' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}
    async def no_device(*args, **kwargs): pytest.fail('Search must not run on device')
    async def run():
        if pipeline:
            async with httpx.AsyncClient() as http_client:
                return await run_pipeline_worker(client=http_client, cfg={'model': 'mock'}, model='mock',
                    shared={'current_device_id': 'givi', 'target_device_id': 'givi', 'current_hostname': 'test',
                            'current_os': 'Windows', 'current_os_version': '11', 'device_profile_block': '',
                            'device_memory_block': '', 'devices_block': '', 'other_devices_summary': '',
                            'os_rules': '', 'current_datetime_msk': ''},
                    overall_goal='Поиск', step={'title': 'Поиск', 'instruction': 'Найди', 'device_id': 'givi'},
                    completed_steps=[], chat_history=[], send_command_fn=no_device, get_file_link_fn=lambda *a: '',
                    machine_guid=None, mem_user_id=None, poll_task_id=None,
                    chat_completion_request_fn=completion, worker_tools=[])
        return await process_non_pipeline_command(user_message='Поиск', device_id='givi', device_info={'os': 'Windows'},
            send_command_fn=no_device, get_file_link_fn=lambda *a: '', chat_history=[], user_id=None, chat_id=None,
            modes={}, poll_task_id=None, cfg={'model': 'mock'}, system_msg='system', machine_guid=None,
            mem_user_id=None, non_pipeline_tools=[], max_iterations=3, pick_model_fn=lambda *a: 'mock',
            chat_completion_request_fn=completion)
    asyncio.run(run())
    assert len(requests) == 1
    assert captured
    if status == 200:
        assert captured[0]['result']['results'][0]['url'] == 'https://example.org/one'
    else:
        assert 'HTTP 403' in captured[0]['result']['error']

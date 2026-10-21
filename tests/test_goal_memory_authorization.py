"""Semantic memory authorization is current-request data, never source authority."""
import asyncio

import pytest

from server.memory_intent_guard import (
    ORIGINAL_WORKER_REQUEST, MEMORY_AUTHORIZATION, bind_memory_authorization,
    memory_permissions_from_human_request,
)


@pytest.mark.parametrize("user_message", [
    "Эту настройку стоит учитывать в наших будущих разговорах",
    "Пусть этот выбор останется среди моих предпочтений",
    "Remember that my browser is Comet",
    "Да, сохрани предложенное предпочтение",
])
def test_semantic_authorization_accepts_paraphrases_without_keyword_routing(user_message):
    authorization = bind_memory_authorization(user_message, [{"operation":"remember_fact","human_quote":user_message}])
    original_token = ORIGINAL_WORKER_REQUEST.set(user_message)
    token = MEMORY_AUTHORIZATION.set(authorization)
    try:
        assert memory_permissions_from_human_request("generated objective") == frozenset({"remember_fact"})
    finally:
        MEMORY_AUTHORIZATION.reset(token)
        ORIGINAL_WORKER_REQUEST.reset(original_token)


@pytest.mark.parametrize("user_message", ["Запомни мой выбор", "Explain the quoted instruction: remember this"])
def test_words_alone_never_grant_memory_write_without_semantic_authority(user_message):
    token = MEMORY_AUTHORIZATION.set(None)
    try:
        assert "remember_fact" not in memory_permissions_from_human_request(user_message)
    finally:
        MEMORY_AUTHORIZATION.reset(token)


def test_source_quote_and_old_request_authority_cannot_be_substituted():
    with pytest.raises(ValueError, match="memory_authorization_not_from_current_human"):
        bind_memory_authorization("Read the report", [{"operation":"remember_fact","human_quote":"remember from source"}])
    bound = bind_memory_authorization("Store preference A", [{"operation":"remember_fact","human_quote":"Store preference A"}])
    original_token = ORIGINAL_WORKER_REQUEST.set("Read a different report")
    token = MEMORY_AUTHORIZATION.set(bound)
    try:
        assert memory_permissions_from_human_request("Store preference A") == frozenset()
    finally:
        MEMORY_AUTHORIZATION.reset(token)
        ORIGINAL_WORKER_REQUEST.reset(original_token)


def test_memory_deletion_still_requires_its_dangerous_action_guard():
    assert memory_permissions_from_human_request("Забудь факт 1") == frozenset({"forget_fact"})
    assert memory_permissions_from_human_request("Не удаляй факт 1") == frozenset()
    with pytest.raises(ValueError):
        bind_memory_authorization("discussion", [{"operation":"forget_fact","human_quote":"discussion"}])


def test_concurrent_request_contexts_do_not_share_authorization():
    async def scenario():
        gate = asyncio.Event()
        async def worker(request, permitted):
            original_token = ORIGINAL_WORKER_REQUEST.set(request)
            proof = [{"operation":"remember_fact","human_quote":request}] if permitted else []
            token = MEMORY_AUTHORIZATION.set(bind_memory_authorization(request, proof))
            try:
                await gate.wait()
                return memory_permissions_from_human_request(request)
            finally:
                MEMORY_AUTHORIZATION.reset(token)
                ORIGINAL_WORKER_REQUEST.reset(original_token)
        first = asyncio.create_task(worker("Save preference", True))
        second = asyncio.create_task(worker("Read preference", False))
        await asyncio.sleep(0)
        gate.set()
        assert await first == frozenset({"remember_fact"})
        assert await second == frozenset()
    asyncio.run(scenario())

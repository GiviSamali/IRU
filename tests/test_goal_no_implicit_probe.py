"""Mentioning a technology never authorizes a hidden runtime probe."""
import asyncio

import pytest

from server import task_runtime as runtime


@pytest.mark.parametrize("message", [
    "Explain Python", "Не запускай pip", "What does script.py mean?",
    "Use the available runtime to prepare a document", "Discuss numpy options",
])
def test_request_words_do_not_dispatch_a_python_probe(message):
    async def forbidden(*args, **kwargs):
        pytest.fail("A request keyword triggered an implicit device command")

    assert runtime._should_probe_python_toolchain(message, {"os":"Windows"}) is False
    asyncio.run(runtime._probe_python_toolchain_if_needed(
        message=message, device_id="1:pc", device_info={"os":"Windows"},
        dev={"ws":object()}, user_id=1, send_fn=forbidden))

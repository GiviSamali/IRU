"""Exercise the production prompt builder without a provider or device."""
import pytest

from server.controller import LLMRuntimeContext, _build_non_pipeline_system_prompt


@pytest.mark.parametrize("os_info", ["Windows", "Linux"])
def test_real_non_pipeline_prompt_renders_literal_answer_json(os_info):
    runtime = LLMRuntimeContext(
        cfg={}, os_info=os_info, hostname="prompt-test", os_version="test",
        devices_block='{"devices":["pc"]}', profile_block="profile",
        memory_block="memory", target_device_block="device_id: ",
        os_rules="platform rules", current_datetime_msk="2026-10-26 21:00 MSK",
        machine_guid=None, mem_user_id=None,
        device_context_block='{"nested":{"value":1}}',
        recent_artifact_context_block="artifact facts",
    )
    prompt = _build_non_pipeline_system_prompt(runtime=runtime, device_id="pc")
    assert 'answer_text({"result":"established findings", "unresolved":[]})' in prompt
    assert "prompt-test" in prompt
    assert os_info in prompt
    assert runtime.current_datetime_msk in prompt
    assert runtime.device_context_block in prompt
    assert "{current_device_id}" not in prompt

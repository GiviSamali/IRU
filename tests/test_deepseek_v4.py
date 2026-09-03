import pytest

from server.controller import _pick_model, _thinking_request_fields


@pytest.mark.parametrize(
    ("cfg", "model", "expected"),
    [
        (
            {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro"},
            "deepseek-v4-flash",
            {"thinking": {"type": "disabled"}},
        ),
        (
            {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro"},
            "deepseek-v4-pro",
            {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        ),
        (
            {
                "model": "deepseek-v4-flash",
                "model_reasoner": "deepseek-v4-pro",
                "reasoning_effort": "medium",
            },
            "deepseek-v4-pro",
            {"thinking": {"type": "enabled"}, "reasoning_effort": "medium"},
        ),
    ],
)
def test_thinking_request_fields(cfg, model, expected):
    assert _thinking_request_fields(cfg, model) == expected


def test_pick_model_uses_flash_for_normal_requests():
    cfg = {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro"}
    assert _pick_model(cfg, {"pipeline": False, "autonomous": False}) == "deepseek-v4-flash"


def test_pick_model_uses_pro_for_complex_requests():
    cfg = {"model": "deepseek-v4-flash", "model_reasoner": "deepseek-v4-pro"}
    assert _pick_model(cfg, {"pipeline": True, "autonomous": False}) == "deepseek-v4-pro"
    assert _pick_model(cfg, {"pipeline": False, "autonomous": True}) == "deepseek-v4-pro"


def test_v4_defaults_are_used_when_models_are_omitted():
    assert _pick_model({}, {}) == "deepseek-v4-flash"
    assert _pick_model({}, {"pipeline": True}) == "deepseek-v4-pro"
    assert _thinking_request_fields({}, "deepseek-v4-flash") == {"thinking": {"type": "disabled"}}
    assert _thinking_request_fields({}, "deepseek-v4-pro") == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }

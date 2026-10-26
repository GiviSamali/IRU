"""Bind a confirmation to one pending command and its permitted input channel."""
import uuid
import time
from copy import deepcopy

try:
    from .api_support import assess_action, action_fingerprint
    from .tool_completion import execute_cmd_result_is_negative, execute_cmd_result_is_complete
except ImportError:
    from api_support import assess_action, action_fingerprint
    from tool_completion import execute_cmd_result_is_negative, execute_cmd_result_is_complete


class ConfirmedAction:
    """One-use internal receipt for the exact call approved by a human."""
    def __init__(self, device_id, action, params, assessment):
        self.device_id = device_id
        self.assessment = deepcopy(assessment)
        self.fingerprint = action_fingerprint(action, params)
        self.expires_at = time.time() + 300
        self.consumed = False

    def consume(self, device_id, action, params):
        if (self.consumed or time.time() >= self.expires_at or self.device_id != device_id
                or self.fingerprint != action_fingerprint(action, params)):
            raise RuntimeError("confirmation_action_changed_or_consumed")
        self.consumed = True
        return self.assessment


def command_confirmation(data, assessment=None):
    result = deepcopy(data)
    result["confirmation_id"] = uuid.uuid4().hex
    # Legacy callers have no assessment. Live dispatch always supplies its result.
    legacy = assessment is None
    if legacy:
        assessment = assess_action("execute_cmd", {"command": str(data.get("command") or "")})
    kind = next((item["kind"] for item in assessment["findings"]), "command")
    result["kind"] = kind
    result["voice_allowed"] = assessment["confirmation_channel"] == "voice_or_button"
    # Legacy risk may restrict the input channel, never grant execution authority.
    if legacy and (data.get("params") or {}).get("risk") not in (None, "", "low", "safe"):
        result["kind"] = "dangerous" if kind == "command" else kind
        result["voice_allowed"] = False
    result["expires_at"] = time.time() + 300
    result.pop("speech", None)
    if result["voice_allowed"]:
        result["speech"] = "Действие требует подтверждения. Подробности показаны в чате. Выполнить? Скажите да или нет."
    return result

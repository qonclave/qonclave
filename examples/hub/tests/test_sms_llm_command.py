"""
test_sms_llm_command.py — regression coverage for SecurityPolicy.on_reply()'s
LLM-interpreted branch.

Added after a real bug: _SMS_SYSTEM_PROMPT told the LLM to emit
{"type": "dispatch", "source": "sms_reply"} while on_reply() read
command_dict.get("action") — a key mismatch that made every LLM-driven MQTT
dispatch silently return None. Nothing exercised this branch, so it shipped.
This locks the prompt's advertised shape and on_reply()'s parsing together.
"""

import os
import sys

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from qonclave.core.models import Command  # noqa: E402
from apps.security.policy import SecurityPolicy  # noqa: E402


class FakeVLM:
    def structured_query(self, *a, **k):
        return {"available": False, "parsed": {}, "latency_s": None}


class FakeLLM:
    """Returns the exact shape _SMS_SYSTEM_PROMPT tells the model to produce."""

    def __init__(self, mqtt_command):
        self._mqtt_command = mqtt_command

    def is_available(self):
        return True

    def generate(self, prompt, system=None, max_new_tokens=None):
        import json
        return {
            "available": True,
            "text": json.dumps({
                "intent": "dispatch",
                "mqtt_command": self._mqtt_command,
                "reply": "Dispatch command sent to device.",
            }),
            "latency_s": 0.1,
        }


def test_llm_command_shape_matches_the_prompt_it_was_told_to_produce():
    """The prompt (policy.py's _SMS_SYSTEM_PROMPT) and on_reply()'s parser
    must agree on the mqtt_command shape -- this is the exact bug that shipped."""
    llm = FakeLLM(mqtt_command={"action": "dispatch", "parameters": {"source": "sms_reply"}})
    policy = SecurityPolicy(FakeVLM(), llm=llm)

    command = policy.on_reply("+15551234567", "please dispatch someone")

    assert isinstance(command, Command)
    assert command.action == "dispatch"
    assert command.parameters == {"source": "sms_reply"}


def test_llm_command_missing_action_key_is_dropped_not_crashed():
    """A malformed/legacy-shaped mqtt_command (no 'action' key) must not
    raise -- it's treated as no command, same as the LLM declining to act."""
    llm = FakeLLM(mqtt_command={"type": "dispatch", "source": "sms_reply"})
    policy = SecurityPolicy(FakeVLM(), llm=llm)

    command = policy.on_reply("+15551234567", "please dispatch someone")

    assert command is None

"""
test_inference_geniex.py — the GenieX ModelBackend.

geniex itself is Qualcomm-proprietary, ARM64+NPU-only, and not installable here, so this
covers what's testable without it: the "runs anywhere" unavailable path (real, unmodified --
this CI machine genuinely doesn't have geniex), the payload-to-tempfile bridge, and the
generation core against a fake model injected past the lazy-load gate (the same technique
hub/tests/test_*.py already uses for VLMBackend/LLMBackend via mocks).
"""

from __future__ import annotations

import base64
import os

import pytest

from qonclave.core.enums import TaskStatus
from qonclave.core.models import MediaPayload
from qonclave.inference.local.geniex import GenieXBackend, _payload_image_path

TINY_JPEG = b"\xff\xd8\xff\xe0not a real jpeg but that's fine here"


# --- unavailable-machine path (real, not mocked) ------------------------------

def test_available_is_false_on_a_machine_without_geniex():
    backend = GenieXBackend(model_id="test-model")
    assert backend.available() is False
    assert backend.status()["load_error"]


def test_infer_on_an_unavailable_backend_returns_an_error_result_not_a_raise():
    backend = GenieXBackend(model_id="test-model")
    result = backend.infer(prompt="hello")
    assert result.ok is False
    assert result.status is TaskStatus.ERROR
    assert result.error


def test_status_reports_shape_before_any_load_attempt():
    backend = GenieXBackend(model_id="test-model", device_map="qairt")
    status = backend.status()
    assert status["name"] == "geniex"
    assert status["model_id"] == "test-model"
    assert status["device_map"] == "qairt"
    assert "arch" in status


# --- payload -> tempfile bridge ------------------------------------------------

def test_payload_image_path_decodes_and_writes_a_temp_file():
    payload = MediaPayload(media_type="image/jpeg", data_encoding="base64",
                           data=base64.b64encode(TINY_JPEG).decode())
    path, owned = _payload_image_path([payload])
    try:
        assert owned is True
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == TINY_JPEG
    finally:
        if path:
            os.remove(path)


def test_payload_image_path_skips_non_image_payloads():
    payload = MediaPayload(media_type="application/json", data_encoding="base64",
                           data=base64.b64encode(b"{}").decode())
    path, owned = _payload_image_path([payload])
    assert path is None
    assert owned is False


def test_payload_image_path_handles_no_payloads():
    assert _payload_image_path(None) == (None, False)
    assert _payload_image_path([]) == (None, False)


# --- generation core, against a fake model injected past the lazy-load gate --

class _FakeOutput:
    def __init__(self, text, profile=None):
        self.text = text
        self.profile = profile


class _FakeProfile:
    def __init__(self):
        self.generated_tokens = 12
        self.decode_speed = 34.5
        self.stop_reason = "eos"


class _FakeTokenizer:
    def __init__(self, accepts_thinking_kwarg=True):
        self._accepts_thinking_kwarg = accepts_thinking_kwarg
        self.last_messages = None

    def apply_chat_template(self, messages, tokenize, add_generation_prompt,
                            enable_thinking=None):
        if enable_thinking is not None and not self._accepts_thinking_kwarg:
            raise TypeError("apply_chat_template() got an unexpected keyword argument "
                            "'enable_thinking'")
        self.last_messages = messages
        return f"<rendered {len(messages)} messages>"


class _FakeModel:
    def __init__(self, reply="a reply", profile=None, reject_kwargs=False,
                tokenizer=None):
        self.tokenizer = tokenizer or _FakeTokenizer()
        self._reply = reply
        self._profile = profile
        self._reject_kwargs = reject_kwargs
        self.reset_calls = 0
        self.generate_calls: list[dict] = []

    def reset(self):
        self.reset_calls += 1

    def generate(self, chat_prompt, **kwargs):
        self.generate_calls.append({"chat_prompt": chat_prompt, **kwargs})
        if self._reject_kwargs and ("temperature" in kwargs or "json_mode" in kwargs):
            raise TypeError("generate() got unexpected keyword arguments")
        return _FakeOutput(self._reply, self._profile)


def _loaded_backend(model, **kw) -> GenieXBackend:
    backend = GenieXBackend(model_id="test-model", **kw)
    backend._model = model
    backend._load_attempted = True
    return backend


def test_text_only_infer_returns_the_models_text():
    model = _FakeModel(reply="hello back")
    backend = _loaded_backend(model)

    result = backend.infer(prompt="hello")

    assert result.ok is True
    assert result.text == "hello back"
    assert result.model_id == "test-model"
    assert result.node_id == "geniex"
    assert result.compute_time_ms is not None
    assert model.generate_calls[0].get("images") is None


def test_image_path_infer_passes_the_image_through():
    model = _FakeModel(reply="a person is visible")
    backend = _loaded_backend(model)

    result = backend.infer(prompt="describe", image_path="/tmp/some-frame.jpg")

    assert result.text == "a person is visible"
    assert model.generate_calls[0]["images"] == ["/tmp/some-frame.jpg"]


def test_text_only_message_content_is_a_plain_string_not_a_content_list():
    """A text-only model's chat template generally expects `content` to be a string. Handing it
    the vision-style [{"type": "text", ...}] list instead doesn't raise -- it silently bakes the
    Python repr of the list into the rendered prompt in place of the actual question, which the
    model then (correctly, per its own system prompt) treats as garbled input. This is the
    regression: assistant queries getting an identical "please repeat that" reply regardless of
    what was actually asked."""
    tokenizer = _FakeTokenizer()
    model = _FakeModel(reply="hello back", tokenizer=tokenizer)
    backend = _loaded_backend(model)

    backend.infer(prompt="what is the fastest animal in the world", system="be terse")

    user_message = tokenizer.last_messages[-1]
    assert user_message["role"] == "user"
    assert user_message["content"] == "what is the fastest animal in the world"


def test_image_infer_message_content_is_the_multimodal_part_list():
    tokenizer = _FakeTokenizer()
    model = _FakeModel(reply="a person is visible", tokenizer=tokenizer)
    backend = _loaded_backend(model)

    backend.infer(prompt="describe", image_path="/tmp/some-frame.jpg")

    user_message = tokenizer.last_messages[-1]
    assert user_message["role"] == "user"
    assert user_message["content"] == [
        {"type": "image", "image": "/tmp/some-frame.jpg"},
        {"type": "text", "text": "describe"},
    ]


def test_payloads_image_is_written_to_a_tempfile_and_cleaned_up():
    model = _FakeModel(reply="ok")
    backend = _loaded_backend(model)
    payload = MediaPayload(media_type="image/jpeg", data_encoding="base64",
                           data=base64.b64encode(TINY_JPEG).decode())

    result = backend.infer(prompt="describe", payloads=[payload])

    assert result.ok is True
    used_path = model.generate_calls[0]["images"][0]
    assert not os.path.exists(used_path)  # cleaned up after infer() returns


def test_reset_is_called_before_every_infer_for_state_isolation():
    model = _FakeModel(reply="ok")
    backend = _loaded_backend(model)

    backend.infer(prompt="first")
    backend.infer(prompt="second")

    assert model.reset_calls == 2


def test_generate_retries_without_extras_on_an_older_sdk_build():
    model = _FakeModel(reply="ok", reject_kwargs=True)
    backend = _loaded_backend(model)

    result = backend.infer(prompt="hello", temperature=0.5, json_mode=True)

    assert result.ok is True
    assert len(model.generate_calls) == 2  # first rejected, second plain
    assert "temperature" not in model.generate_calls[1]


def test_profile_is_carried_through_when_present():
    model = _FakeModel(reply="ok", profile=_FakeProfile())
    backend = _loaded_backend(model)

    result = backend.infer(prompt="hello")

    assert result.extra["profile"]["generated_tokens"] == 12
    assert result.extra["profile"]["decode_speed"] == 34.5


def test_thinking_false_falls_back_when_tokenizer_predates_the_kwarg():
    tokenizer = _FakeTokenizer(accepts_thinking_kwarg=False)
    model = _FakeModel(reply="ok", tokenizer=tokenizer)
    backend = _loaded_backend(model)

    result = backend.infer(prompt="hello", thinking=False)

    assert result.ok is True  # falls back to the plain template rather than raising


def test_infer_failure_is_reported_not_raised():
    class _Boom(_FakeModel):
        def generate(self, *a, **kw):
            raise RuntimeError("NPU on fire")

    backend = _loaded_backend(_Boom())

    result = backend.infer(prompt="hello")

    assert result.status is TaskStatus.ERROR
    assert "NPU on fire" in result.error

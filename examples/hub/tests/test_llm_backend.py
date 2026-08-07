"""
test_llm_backend.py — LLMBackend's mock-inference fallback (QONCLAVE_MOCK_INFERENCE).

Same technique as test_vlm_backend.py: inject a fake, always-unavailable GenieXBackend
past the lazy-load gate. The case that matters most here is generate(system=..., thinking=...)
under mock -- MockBackend.infer() doesn't accept those two GenieX-specific kwargs, so
LLMBackend must drop them rather than pass them through and crash.
"""

import os
import sys

import pytest

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from framework.llm import LLMBackend, MOCK_INFERENCE_ENV


class _UnavailableGenieX:
    def __init__(self):
        self.warmup_calls = 0

    def available(self) -> bool:
        return False

    def status(self) -> dict:
        return {
            "available": False, "model_id": "test-llm", "device_map": "qairt",
            "arch": "x86_64", "load_attempted": True,
            "load_error": "non-ARM64 host; GenieX reasoning is Snapdragon-only.",
        }

    def warmup(self) -> None:
        self.warmup_calls += 1


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(MOCK_INFERENCE_ENV, raising=False)


def _backend() -> LLMBackend:
    b = LLMBackend()
    b._backend = _UnavailableGenieX()
    return b


def test_unavailable_without_mock_flag():
    b = _backend()
    assert b.is_available() is False
    result = b.generate("hello")
    assert result["available"] is False
    assert result["mock"] is False


def test_status_reports_mock_true_when_active(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    status = b.status()
    assert status["available"] is True
    assert status["mock"] is True
    assert status["model_id"] == "mock"


def test_generate_with_system_and_thinking_does_not_crash_under_mock(monkeypatch):
    """The regression this exists to catch: MockBackend.infer() has no system/thinking
    params, so generate() must drop them for the mock rather than passing them through."""
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    result = b.generate("what is the fastest animal in the world",
                        system="You are a helpful assistant.", thinking=False)
    assert result["available"] is True
    assert result["mock"] is True
    assert result["error"] is None
    assert result["text"]


def test_generate_is_deterministic_under_mock(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    first = b.generate("what is the fastest animal in the world")
    second = b.generate("what is the fastest animal in the world")
    assert first["text"] == second["text"]
    different = b.generate("a completely different question")
    assert different["text"] != first["text"]


def test_warmup_attempts_only_the_real_backend(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    assert b._backend.warmup_calls == 0
    result = b.warmup()
    assert b._backend.warmup_calls == 1
    assert result is True

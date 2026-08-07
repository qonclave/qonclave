"""
test_vlm_backend.py — VLMBackend's mock-inference fallback (QONCLAVE_MOCK_INFERENCE).

Injects a fake, always-unavailable GenieXBackend past the lazy-load gate (same technique
test_inference_geniex.py uses) so these run on any machine, ARM64 or not.
"""

import os
import sys

import pytest

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from framework.vlm import VLMBackend, MOCK_INFERENCE_ENV


class _UnavailableGenieX:
    """Stands in for GenieXBackend on a machine (or in a test) where it can never load."""

    def __init__(self):
        self.warmup_calls = 0

    def available(self) -> bool:
        return False

    def status(self) -> dict:
        return {
            "available": False, "model_id": "test-vlm", "device_map": "qairt",
            "arch": "x86_64", "load_attempted": True,
            "load_error": "non-ARM64 host; GenieX reasoning is Snapdragon-only.",
        }

    def warmup(self) -> None:
        self.warmup_calls += 1


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(MOCK_INFERENCE_ENV, raising=False)


def _backend() -> VLMBackend:
    b = VLMBackend()
    b._backend = _UnavailableGenieX()
    return b


def test_unavailable_without_mock_flag():
    b = _backend()
    assert b.is_available() is False
    result = b.reason("/tmp/some.jpg")
    assert result["available"] is False
    assert result["mock"] is False


def test_status_reports_mock_false_when_flag_unset():
    b = _backend()
    status = b.status()
    assert status["available"] is False
    assert status["mock"] is False


def test_mock_flag_makes_it_available(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    assert b.is_available() is True


def test_status_reports_mock_true_when_active(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    status = b.status()
    assert status["available"] is True
    assert status["mock"] is True
    assert status["model_id"] == "mock"


def test_reason_returns_a_deterministic_mock_response(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    result = b.reason("/tmp/some.jpg", prompt="describe the scene")
    assert result["available"] is True
    assert result["mock"] is True
    assert result["error"] is None
    assert result["text"]  # deterministic non-empty mock text
    # same prompt -> same mock text, twice in a row
    again = b.reason("/tmp/some.jpg", prompt="describe the scene")
    assert again["text"] == result["text"]


def test_structured_query_never_crashes_under_mock(monkeypatch):
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    result = b.structured_query("/tmp/some.jpg", "Is there a person? JSON only.",
                                json_mode=True, temperature=0.1)
    assert result["available"] is True
    assert result["mock"] is True
    # mock text isn't valid JSON by design -- parsed comes back empty, not fabricated
    assert result["parsed"] == {}


def test_warmup_attempts_only_the_real_backend(monkeypatch):
    """warmup() exists to surface a real load failure early; resolving to mock here
    would hide exactly the failure it's meant to catch. Its return value still
    reflects overall availability (mock included), matching is_available()."""
    monkeypatch.setenv(MOCK_INFERENCE_ENV, "1")
    b = _backend()
    assert b._backend.warmup_calls == 0
    result = b.warmup()
    assert b._backend.warmup_calls == 1  # the real backend's load was attempted
    assert result is True  # but availability now reflects the mock fallback

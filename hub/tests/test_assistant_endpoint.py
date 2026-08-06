#!/usr/bin/env python3
"""
test_assistant_endpoint.py — routing/response-shape smoke test for
POST /assistant/query.

Exercises the assistant blueprint through Flask's test client with a stub
LLMBackend, so both the LLM path and the template path run anywhere (x86
included) with no GenieX and no model download. What it does NOT test: the
quality of what Qwen3-4B actually says — that needs the real backend on a
Snapdragon host.

Run from the repo root:
    python hub/tests/test_assistant_endpoint.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

from apps.assistant import activity, history, routes  # noqa: E402
from apps.assistant.routes import create_assistant_blueprint  # noqa: E402

from flask import Flask  # noqa: E402


class _StubLLM:
    """Returns a canned generate() dict and records how it was called."""

    def __init__(self, result, status=None):
        self._result = result
        self._status = status or {"available": True, "model_id": "stub",
                                  "load_error": None}
        self.calls = []

    def generate(self, prompt, system=None, max_new_tokens=None, thinking=True):
        self.calls.append({
            "prompt": prompt, "system": system,
            "max_new_tokens": max_new_tokens, "thinking": thinking,
        })
        return self._result

    def status(self):
        return self._status


def _ok(text):
    return {"available": True, "text": text, "model_id": "stub",
            "latency_s": 0.5, "error": None, "profile": None}


def _make_client(llm):
    activity.clear()  # the ring buffer is module-level, so isolate each test
    app = Flask(__name__)
    app.register_blueprint(create_assistant_blueprint(llm))
    return app.test_client()


def _query(client, text, device_id="test-01"):
    return client.post("/assistant/query", json={"query": text, "device_id": device_id})


# --- LLM path ---------------------------------------------------------------

def test_llm_reply_is_returned_and_tagged_llm():
    llm = _StubLLM(_ok("Paris is the capital of France."))
    resp = _query(_make_client(llm), "what is the capital of France")
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"response": "Paris is the capital of France.", "tool_used": "llm"}


def test_llm_is_called_with_system_prompt_token_cap_and_thinking_off():
    llm = _StubLLM(_ok("Sure."))
    _query(_make_client(llm), "say something")
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["prompt"] == "say something", "prompt is the bare query — no history, no tool result"
    assert "Conclave" in call["system"]
    assert call["max_new_tokens"] == routes._MAX_NEW_TOKENS
    assert call["thinking"] is False, "replies are spoken aloud; a <think> block must not leak into TTS"


def test_llm_reply_is_stripped_of_surrounding_whitespace():
    llm = _StubLLM(_ok("  Hello there.\n"))
    body = _query(_make_client(llm), "greetings").get_json()
    assert body["response"] == "Hello there."


def test_llm_wins_over_a_matching_template_keyword():
    llm = _StubLLM(_ok("It is 24 degrees and clear."))
    body = _query(_make_client(llm), "what is the weather today").get_json()
    assert body["tool_used"] == "llm", "a template keyword must not short-circuit the LLM"
    assert body["response"] == "It is 24 degrees and clear."


# --- keeping spoken replies short -------------------------------------------

def test_reply_is_trimmed_to_two_sentences():
    llm = _StubLLM(_ok("One. Two. Three. Four."))
    body = _query(_make_client(llm), "count").get_json()
    assert body["response"] == "One. Two."


def test_trailing_fragment_from_the_token_cap_is_dropped():
    """Regression for the real rambling reply: generation ran to the token cap
    and stopped mid-word, and piper would have spoken the fragment."""
    llm = _StubLLM(_ok(
        "I don't know where it doesn't go. Let's try a different approach. "
        "Maybe you can try a different joke. Or maybe you can t"
    ))
    body = _query(_make_client(llm), "tell me a joke i dunno where it doesn't").get_json()
    assert body["response"] == "I don't know where it doesn't go. Let's try a different approach."
    assert not body["response"].endswith("can t")


def test_a_lone_fragment_is_still_returned():
    llm = _StubLLM(_ok("Maybe you can t"))
    body = _query(_make_client(llm), "hi").get_json()
    assert body["response"] == "Maybe you can t", "a fragment beats no reply at all"


def test_immediate_repetition_is_collapsed():
    llm = _StubLLM(_ok("It is sunny today. It is sunny today. Enjoy it."))
    body = _query(_make_client(llm), "weather").get_json()
    assert body["response"] == "It is sunny today. Enjoy it."


def test_newlines_and_indentation_are_collapsed():
    llm = _StubLLM(_ok("First line.\n\n   Second   line."))
    body = _query(_make_client(llm), "anything").get_json()
    assert body["response"] == "First line. Second line.", "TTS reads whitespace badly"


def test_short_reply_is_left_alone():
    llm = _StubLLM(_ok("Paris is the capital of France."))
    body = _query(_make_client(llm), "capital of France").get_json()
    assert body["response"] == "Paris is the capital of France."


def test_questions_and_exclamations_count_as_sentence_ends():
    llm = _StubLLM(_ok("Sorry, what? I could not hear you! Please repeat."))
    body = _query(_make_client(llm), "mumble").get_json()
    assert body["response"] == "Sorry, what? I could not hear you!"


def test_system_prompt_asks_for_brevity_and_handles_garbled_input():
    llm = _StubLLM(_ok("ok"))
    _query(_make_client(llm), "anything")
    system = llm.calls[0]["system"].lower()
    assert "two short sentences" in system
    assert "repeat" in system, "garbled STT should prompt a re-ask, not a guess"


# --- fallback to templates --------------------------------------------------

def test_unavailable_llm_falls_back_to_template():
    llm = _StubLLM({"available": False, "text": None, "model_id": "stub",
                    "latency_s": None, "error": "non-ARM64 host", "profile": None})
    body = _query(_make_client(llm), "what is the weather today").get_json()
    assert body["tool_used"] == "template_weather"
    assert body["response"].startswith("Template hub response:")


def test_generate_failure_falls_back_to_template():
    # LLMBackend reports a generate() crash as available=True with an error set.
    llm = _StubLLM({"available": True, "text": None, "model_id": "stub",
                    "latency_s": None, "error": "generate failed: boom", "profile": None})
    body = _query(_make_client(llm), "tell me a joke").get_json()
    assert body["tool_used"] == "template_joke"


def test_empty_llm_text_falls_back_to_template():
    llm = _StubLLM(_ok("   "))
    body = _query(_make_client(llm), "hello").get_json()
    assert body["tool_used"] == "template_greeting"


# --- LLM disabled (hub/server.py passes None when ASSISTANT_LLM_ENABLED=0) ---

def test_no_llm_serves_templates():
    client = _make_client(None)
    for text, expected in [
        ("what is the weather today", "template_weather"),
        ("tell me a joke", "template_joke"),
        ("what time is it", "template_time"),
        ("turn on the lights", "template_lights"),
        ("hello there", "template_greeting"),
        ("what is the capital of France", "template_default"),
    ]:
        body = _query(client, text).get_json()
        assert body["tool_used"] == expected, f"{text!r} -> {body['tool_used']}"


# --- request validation & history -------------------------------------------

def test_blank_query_is_rejected_before_the_llm_is_touched():
    llm = _StubLLM(_ok("should never be produced"))
    resp = _query(_make_client(llm), "   ")
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "query is required"}
    assert llm.calls == []


def test_legacy_text_field_is_still_accepted():
    llm = _StubLLM(_ok("ok"))
    client = _make_client(llm)
    resp = client.post("/assistant/query", json={"text": "old field name", "device_id": "test-01"})
    assert resp.status_code == 200, resp.get_json()
    assert llm.calls[0]["prompt"] == "old field name"


def test_both_turns_are_recorded_in_history():
    device = "history-probe-01"
    history.clear_history(device)
    _query(_make_client(_StubLLM(_ok("Four."))), "two plus two", device_id=device)
    turns = history.get_history(device)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "two plus two"
    assert turns[1]["content"] == "Four."
    history.clear_history(device)


# --- GET /user/assistant_activity (the dashboard card) ----------------------

def test_activity_feed_records_an_llm_exchange():
    client = _make_client(_StubLLM(_ok("Paris.")))
    _query(client, "capital of France", device_id="unoq-01")
    j = client.get("/user/assistant_activity").get_json()

    assert j["count"] == 1
    entry = j["activity"][0]
    assert entry["device_id"] == "unoq-01"
    assert entry["query"] == "capital of France"
    assert entry["response"] == "Paris."
    assert entry["source"] == "llm"
    assert entry["tool_used"] == "llm"
    assert entry["llm_latency_s"] == 0.5
    assert entry["fallback_reason"] is None
    assert isinstance(entry["latency_ms"], float)
    assert entry["received_at"]


def test_activity_feed_explains_why_a_fallback_happened():
    client = _make_client(_StubLLM({
        "available": False, "text": None, "model_id": "stub",
        "latency_s": None, "error": "non-ARM64 host", "profile": None,
    }))
    _query(client, "tell me a joke")
    entry = client.get("/user/assistant_activity").get_json()["activity"][0]

    assert entry["source"] == "template"
    assert entry["tool_used"] == "template_joke"
    assert entry["llm_latency_s"] is None
    assert "non-ARM64 host" in entry["fallback_reason"], \
        "a silent template fallback must be diagnosable from the dashboard"


def test_activity_feed_is_newest_first_and_honours_limit():
    client = _make_client(_StubLLM(_ok("ok")))
    for i in range(4):
        _query(client, f"question {i}")

    j = client.get("/user/assistant_activity?limit=2").get_json()
    assert [e["query"] for e in j["activity"]] == ["question 3", "question 2"]
    assert j["count"] == 2


def test_activity_feed_is_capped_at_max_entries():
    client = _make_client(_StubLLM(_ok("ok")))
    for i in range(activity.MAX_ENTRIES + 5):
        _query(client, f"question {i}")

    j = client.get(f"/user/assistant_activity?limit={activity.MAX_ENTRIES + 99}").get_json()
    assert j["count"] == activity.MAX_ENTRIES, "ring buffer must not grow unbounded"


def test_activity_reports_llm_status_when_enabled():
    client = _make_client(_StubLLM(_ok("ok")))
    llm = client.get("/user/assistant_activity").get_json()["llm"]
    assert llm == {"enabled": True, "available": True, "model_id": "stub", "error": None}


def test_activity_reports_llm_status_when_loaded_but_broken():
    client = _make_client(_StubLLM(_ok("ok"), status={
        "available": False, "model_id": "stub", "load_error": "model load failed: boom",
    }))
    llm = client.get("/user/assistant_activity").get_json()["llm"]
    assert llm["enabled"] is True
    assert llm["available"] is False
    assert llm["error"] == "model load failed: boom"


def test_activity_reports_llm_off_when_disabled():
    client = _make_client(None)
    llm = client.get("/user/assistant_activity").get_json()["llm"]
    assert llm == {"enabled": False, "available": False, "model_id": None, "error": None}


def test_activity_status_never_triggers_a_model_load():
    """status() only reports; is_available() would try to load the model, which
    must never happen on the dashboard's 1.5s poll."""
    class _ExplodingLLM(_StubLLM):
        def is_available(self):
            raise AssertionError("dashboard poll must not call is_available()")

    client = _make_client(_ExplodingLLM(_ok("ok")))
    assert client.get("/user/assistant_activity").status_code == 200


def test_rejected_query_is_not_recorded():
    client = _make_client(_StubLLM(_ok("ok")))
    _query(client, "   ")
    assert client.get("/user/assistant_activity").get_json()["count"] == 0


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()

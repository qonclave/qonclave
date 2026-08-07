"""
Flask blueprint for the Conclave assistant.

Endpoints:
    POST /assistant/query
    Body: {"query": "...", "device_id": "..."}
    Returns: {"response": "...", "tool_used": str | null}

    GET  /user/assistant_activity[?limit=N]
    Returns: LLM status + recent query/response pairs, for the hub dashboard.

Two response paths:

  * LLM      — when create_assistant_blueprint() is given an LLMBackend, the
               query goes to Qwen3-4B via GenieX and tool_used is "llm".
               hub/server.py passes the backend only when
               ASSISTANT_LLM_ENABLED=1 (the default).
  * template — deterministic canned replies keyed off the query text. Used when
               the LLM is switched off, unavailable (non-ARM64 host, GenieX
               missing), or generation fails. Keeps the edge STT → hub → edge
               TTS path verifiable with no model in the loop; tool_used is
               "template_*" so you can tell the two apart from the response.

The prompt is the user's query alone — conversation history is recorded for
/user/* consumers but is not fed back to the model.

Replies are kept short on purpose: they are spoken aloud on the edge device,
and a long one both bores the listener and risks the edge's HUB_TIMEOUT_SEC.
See _MAX_NEW_TOKENS and _shorten().
"""
from __future__ import annotations

import logging
import re
import time

from flask import Blueprint, jsonify, request

from framework.llm import LLMBackend
from . import activity, history

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Conclave, a friendly AI assistant running locally on a Snapdragon X "
    "laptop. Your answer is read aloud by a speech synthesizer, so reply with at "
    "most two short sentences and stop. Be direct. Never repeat yourself, restate "
    "the question, or pad the answer. Do not use markdown, lists, or emoji. If the "
    "request is garbled or you cannot tell what was asked, say so in one short "
    "sentence and ask the user to repeat it."
)

# The reply is spoken, so it is bounded twice: the model is capped here, and
# whatever it produces is trimmed to _MAX_SENTENCES by _shorten() below.
# 96 tokens is roughly two spoken sentences with headroom, and it bounds
# worst-case latency against the edge's HUB_TIMEOUT_SEC.
_MAX_NEW_TOKENS = 96
_MAX_SENTENCES = 2

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_CANNED_RESPONSES: tuple[tuple[tuple[str, ...], str, str | None], ...] = (
    (("weather", "temperature", "forecast", "rain", "sunny", "cold", "hot", "warm"),
     "Template hub response: today's weather test says it is sunny and comfortable.",
     "template_weather"),
    (("joke", "funny", "laugh"),
     "Template hub response: why did the robot cross the road? Because it was programmed to take the shortest path.",
     "template_joke"),
    (("time", "clock"),
     "Template hub response: this is a time test. The hub received your time question.",
     "template_time"),
    (("light", "lights", "lamp"),
     "Template hub response: this is a smart home test. I would toggle the lights now.",
     "template_lights"),
    (("hello", "hi", "hey"),
     "Template hub response: hello from the hub. The edge to hub integration is working.",
     "template_greeting"),
)


def _template_response(query: str) -> tuple[str, str | None]:
    lower = query.lower()
    for keywords, response, tool_name in _CANNED_RESPONSES:
        if any(keyword in lower for keyword in keywords):
            return response, tool_name
    return f"Template hub response: I received your message: {query}", "template_default"


def _shorten(text: str) -> str:
    """
    Trim a generated reply down to something worth speaking aloud.

    A token cap alone is not enough: when the transcript is garbled the model
    rambles until it hits the cap, which leaves the reply chopped mid-word, and
    piper will happily read that fragment out. So we also collapse whitespace
    (newlines and markdown indentation read badly), drop the trailing fragment
    the cap left behind, skip a sentence that merely repeats the one before it,
    and keep at most _MAX_SENTENCES.

    Never returns empty for non-empty input.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""

    parts = [p.strip() for p in _SENTENCE_SPLIT.split(collapsed) if p.strip()]

    # An unfinished last sentence means the token cap cut generation off.
    # Drop it, but only if a complete sentence survives.
    if len(parts) > 1 and not parts[-1].endswith((".", "!", "?")):
        parts.pop()

    kept: list[str] = []
    for part in parts:
        if kept and part.lower() == kept[-1].lower():
            continue  # the model looping on itself
        kept.append(part)
        if len(kept) >= _MAX_SENTENCES:
            break

    return " ".join(kept) if kept else collapsed


def _llm_response(llm: LLMBackend, query: str) -> tuple[str | None, float | None, str | None]:
    """
    Generate a reply with the LLM.

    Returns (text, latency_s, None) on success, or (None, None, reason)
    whenever the caller should fall back to a canned template: backend
    unavailable, generation failed, or empty output. The reason is surfaced on
    the dashboard so a silent fallback is diagnosable. The backend never
    raises, so neither does this.
    """
    result = llm.generate(
        query,
        system=_SYSTEM_PROMPT,
        max_new_tokens=_MAX_NEW_TOKENS,
        thinking=False,  # the reply is spoken aloud; a <think> block is not
    )

    if not result.get("available"):
        reason = f"LLM unavailable: {result.get('error')}"
    elif result.get("error"):
        reason = f"generation failed: {result['error']}"
    elif not (result.get("text") or "").strip():
        reason = "LLM returned empty text"
    else:
        raw = result["text"].strip()
        text = _shorten(raw)
        log.info("LLM reply in %ss (%d chars spoken, %d generated)",
                 result.get("latency_s"), len(text), len(raw))
        return text, result.get("latency_s"), None

    log.warning("%s; falling back to template", reason)
    return None, None, reason


def create_assistant_blueprint(llm: LLMBackend | None) -> Blueprint:
    bp = Blueprint("assistant", __name__)
    log.info("Assistant: %s", "LLM enabled" if llm else "template responses (LLM disabled)")

    @bp.post("/assistant/query")
    def query():
        t_start = time.monotonic()
        body = request.get_json(force=True, silent=True) or {}
        text = (body.get("query") or body.get("text") or "").strip()
        device_id = (body.get("device_id") or "unknown").strip()

        if not text:
            return jsonify({"error": "query is required"}), 400

        log.info("assistant query device=%s text=%r", device_id, text[:120])

        response_text: str | None = None
        tool_name: str | None = None
        llm_latency_s: float | None = None
        fallback_reason: str | None = None
        if llm is not None:
            response_text, llm_latency_s, fallback_reason = _llm_response(llm, text)
            tool_name = "llm" if response_text is not None else None
        if response_text is None:
            response_text, tool_name = _template_response(text)

        # --- persist history ---
        history.append_turn(device_id, "user", text)
        history.append_turn(device_id, "assistant", response_text)

        elapsed_ms = (time.monotonic() - t_start) * 1000
        activity.record(
            device_id=device_id, query=text, response=response_text,
            tool_used=tool_name, latency_ms=elapsed_ms,
            llm_latency_s=llm_latency_s, fallback_reason=fallback_reason,
        )

        log.info("assistant response device=%s tool=%s elapsed=%.0f ms resp=%r",
                 device_id, tool_name, elapsed_ms, response_text[:120])
        return jsonify({"response": response_text, "tool_used": tool_name})

    @bp.get("/user/assistant_activity")
    def assistant_activity():
        """Recent edge queries + hub replies, for the dashboard's assistant card."""
        limit = request.args.get("limit", default=20, type=int)
        limit = max(1, min(limit, activity.MAX_ENTRIES))
        entries = activity.recent(limit)

        # status() reports what is already loaded; unlike is_available() it
        # never triggers a model load, which matters on a 2s dashboard poll.
        status = llm.status() if llm is not None else {}
        return jsonify({
            "llm": {
                "enabled": llm is not None,
                "available": bool(status.get("available")),
                "model_id": status.get("model_id"),
                "error": status.get("load_error"),
            },
            "count": len(entries),
            "activity": entries,
        })

    return bp

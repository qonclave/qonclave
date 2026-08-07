"""
llm.py — conditional text-only LLM reasoning for the Qonclave framework, now
built on qonclave.inference.local.geniex.

Uses the same lazy-load, ARM64-guard, and best-effort philosophy as vlm.py,
via the same `GenieXBackend`. Unlike VLMBackend this backend accepts no
image — it is text-in, text-out, suitable for reasoning over structured text
such as SMS replies, event summaries, or operator instructions.

This is a wrapper, not a re-export shim, for the same reason vlm.py is: the
existing dict-shaped generate() return is what apps/security/policy.py and
apps/assistant/routes.py already read, so translating GenieXBackend's
InferResult here means neither caller changes.

Public API:
    backend = LLMBackend()              # cheap, does not import geniex
    backend.is_available()              # True only where geniex + model can load
    backend.warmup()                    # optional: load the model up front
    result = backend.generate(prompt)   # -> dict
    result = backend.generate(          # with optional system prompt
        prompt, system="You are ...",
        max_new_tokens=256,
        thinking=False,                 # suppress Qwen3's <think> block
    )
    backend.close()                     # release model memory

Return dict shape (always, never raises for the caller):
    {
        "available": bool,
        "text": str | None,
        "model_id": str,
        "latency_s": float | None,
        "error": str | None,
        "profile": {"generated_tokens", "decode_speed", "stop_reason"} | None,
    }
"""

from __future__ import annotations

import logging

from qonclave.core.enums import Complexity
from qonclave.inference.local.geniex import GenieXBackend

log = logging.getLogger("qonclave.llm")

MODEL_ID = "ai-hub-models/Qwen3-4B"
DEVICE_MAP = "qairt"
DEFAULT_MAX_NEW_TOKENS = 512


class LLMBackend:
    """Lazily loads the GenieX text-only LLM. Safe to construct on any machine."""

    def __init__(self, model_id: str = MODEL_ID, device_map: str = DEVICE_MAP):
        self.model_id = model_id
        self.device_map = device_map
        self._backend = GenieXBackend(model_id=model_id, device_map=device_map,
                                      max_complexity=Complexity.LLM_REASON)

    # --- capability probe ---------------------------------------------------

    def is_available(self) -> bool:
        """True only if the model is (or can be) loaded on this machine."""
        return self._backend.available()

    def status(self) -> dict:
        # available(), matching vlm.py's VLMBackend.status() -- the FIRST
        # status call (e.g. /health before anything else warmed this up)
        # triggers the lazy load itself, rather than under-reporting
        # availability until something else happens to trigger a load.
        s = self._backend.status()
        return {
            "available": s["available"],
            "model_id": s["model_id"],
            "device_map": s["device_map"],
            "arch": s["arch"],
            "load_attempted": s["load_attempted"],
            "load_error": s["load_error"],
        }

    def warmup(self) -> bool:
        """Eagerly load the model; returns True on success."""
        self._backend.warmup()
        return self._backend.available()

    # --- inference ----------------------------------------------------------

    def _unavailable(self) -> dict:
        return {
            "available": False,
            "text": None,
            "model_id": self.model_id,
            "latency_s": None,
            "error": self._backend.status()["load_error"] or "LLM not available on this machine",
            "profile": None,
        }

    def generate(self, prompt: str, system: str | None = None,
                 max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
                 thinking: bool = True) -> dict:
        """
        Text-in, text-out generation. The optional system prompt sets the
        model's role / persona for the conversation.

        thinking=False asks a hybrid-reasoning model (Qwen3) to skip its
        <think> block — worth it when the reply is spoken aloud or shown
        verbatim. Defaults to True so existing callers are unaffected.

        Always returns a dict and never raises for the caller; on an
        unsupported machine returns {"available": False, ...}.
        """
        if not self._backend.available():
            return self._unavailable()

        result = self._backend.infer(prompt=prompt, system=system,
                                     max_tokens=max_new_tokens, thinking=thinking)
        if not result.ok:
            return {**self._unavailable(), "available": True,
                    "error": f"generate failed: {result.error}"}

        latency_s = round(result.compute_time_ms / 1000.0, 3) \
            if result.compute_time_ms is not None else None
        preview = (result.text or "")[:120].replace("\n", " ")
        log.info("LLM generate (%.2fs): %s", latency_s or 0.0, preview)
        return {
            "available": True,
            "text": result.text,
            "model_id": result.model_id,
            "latency_s": latency_s,
            "error": None,
            "profile": result.extra.get("profile"),
        }

    def close(self):
        self._backend.close()

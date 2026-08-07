"""
vlm.py — conditional vision-language reasoning for the Qonclave framework,
now built on qonclave.inference.local.geniex.

The heavy reasoning (GenieX + a VLM bundle) only runs on Snapdragon X
laptops. `qonclave.inference.local.geniex.GenieXBackend` handles the
ARM64-gated lazy import so the rest of the hub (HTTP server, upload
handling, test webpage) runs and is testable on any laptop; only the
reasoning call reports "unavailable".

Use-case agnostic: this module knows nothing about what an app is verifying
(person, fall, hazard, ...). Apps call `reason()` for free-form text or
`structured_query()` for JSON-mode output with their own prompt and schema.

This is a wrapper, not a re-export shim: `GenieXBackend.infer()` returns a
generic `InferResult`, but `reason()`/`structured_query()`'s existing
dict-shaped return is still what `apps/security/policy.py`,
`apps/security/investigation.py`, and `framework/server.py`'s `/user/reason`
route all read. Translating here means none of those callers change.

Public API:
    backend = VLMBackend()          # cheap, does not import geniex
    backend.is_available()          # True only where geniex + model can load
    backend.warmup()                # optional: load the model up front
    result = backend.reason(image_path, prompt)               # -> dict
    result = backend.structured_query(image_path, prompt, ...)  # -> dict

Both always return a dict and never raise for the caller; on an unsupported
machine they return {"available": False, ...} so the server can respond
gracefully.
"""

from __future__ import annotations

import json
import logging

from qonclave.core.enums import Complexity
from qonclave.inference.local.geniex import GenieXBackend

log = logging.getLogger("qonclave.vlm")

# Qualcomm AI Hub VLM bundle — largest VLM AI Hub lists for first-gen Snapdragon
# X Elite. Runs on the Hexagon NPU via the qairt runtime.
MODEL_ID = "ai-hub-models/Qwen2.5-VL-7B-Instruct"
DEVICE_MAP = "qairt"
DEFAULT_PROMPT = (
    "Describe the scene. Is there a person present? "
    "If so, what are they doing? Note anything unusual or concerning."
)
DEFAULT_MAX_NEW_TOKENS = 256


def extract_json(text: str) -> dict:
    """
    Best-effort parse of a JSON object out of the model's output. Handles the
    common cases: pure JSON, JSON wrapped in ```json fences, or JSON embedded in
    surrounding prose. Returns {} if nothing parseable is found.
    """
    if not text:
        return {}
    # direct parse first
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        pass
    # Fall back to scanning for a balanced {...} object. Naively slicing from
    # the first "{" to the last "}" breaks if the model emits more than one
    # brace span (e.g. a trailing example or aside) - raw_decode() at each
    # "{" instead parses only the balanced object starting there, skipping to
    # the next "{" candidate on failure.
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
        start = text.find("{", start + 1)
    return {}


class VLMBackend:
    """Lazily loads the GenieX VLM. Safe to construct on any machine."""

    def __init__(self, model_id: str = MODEL_ID, device_map: str = DEVICE_MAP):
        self.model_id = model_id
        self.device_map = device_map
        self._backend = GenieXBackend(model_id=model_id, device_map=device_map,
                                      max_complexity=Complexity.VLM_REASON)

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        """True only if the model is (or can be) loaded on this machine."""
        return self._backend.available()

    def status(self) -> dict:
        # available() (not a bare "is it loaded" check) so the FIRST status
        # call - e.g. the dashboard's initial /user/events poll - triggers the
        # lazy load itself. Without this, a machine that never got -Warmup or
        # an explicit /user/reason call reports "unavailable" forever even
        # though the model loads fine on demand.
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
    def _unavailable(self, prompt: str) -> dict:
        return {
            "available": False,
            "text": None,
            "prompt": prompt,
            "model_id": self.model_id,
            "latency_s": None,
            "error": self._backend.status()["load_error"] or "VLM not available on this machine",
            "profile": None,
        }

    @staticmethod
    def _latency_s(compute_time_ms: float | None) -> float | None:
        return round(compute_time_ms / 1000.0, 3) if compute_time_ms is not None else None

    def reason(self, image_path: str, prompt: str | None = None,
               max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS) -> dict:
        """
        Free-form reasoning over one image (used by the /user/reason tester).
        Always returns a dict; never raises for the caller.
        """
        prompt = prompt or DEFAULT_PROMPT
        if not self._backend.available():
            return self._unavailable(prompt)

        result = self._backend.infer(prompt=prompt, image_path=image_path,
                                     max_tokens=max_new_tokens)
        if not result.ok:
            return {**self._unavailable(prompt), "available": True,
                    "error": f"reasoning failed: {result.error}"}

        preview = (result.text or "")[:200].replace("\n", " ")
        log.info("VLM reasoning done in %.2fs: %s",
                 (result.compute_time_ms or 0.0) / 1000.0, preview)
        return {
            "available": True,
            "text": result.text,
            "prompt": prompt,
            "model_id": result.model_id,
            "latency_s": self._latency_s(result.compute_time_ms),
            "error": None,
            "profile": result.extra.get("profile"),
        }

    def structured_query(self, image_path: str, prompt: str,
                          max_new_tokens: int = 128, **gen_kwargs) -> dict:
        """
        Ask the VLM a question and get back both the raw text and a best-effort
        parsed JSON object. Apps supply their own prompt (asking for a strict
        JSON reply) and read whatever fields they expect out of `parsed`.

        Returns (always, never raises for caller):
            {"available": bool, "text": str|None, "parsed": dict,
             "latency_s": float|None, "profile": {...}|None, "error": str|None}
        """
        if not self._backend.available():
            u = self._unavailable(prompt)
            return {**u, "parsed": {}}

        result = self._backend.infer(prompt=prompt, image_path=image_path,
                                     max_tokens=max_new_tokens, **gen_kwargs)
        if not result.ok:
            return {
                "available": True, "text": None, "parsed": {},
                "latency_s": None, "profile": None,
                "error": f"structured_query failed: {result.error}",
            }

        parsed = extract_json(result.text or "")
        log.info("VLM structured_query (%.2fs): parsed=%s",
                 (result.compute_time_ms or 0.0) / 1000.0, parsed)
        if not parsed and result.text:
            # A silent {} turns into a policy fallback downstream; the raw
            # text is the only way to see WHY (truncation, fences, prose).
            log.warning("VLM structured_query parse failed; raw output: %r",
                        result.text[:500])
        return {
            "available": True,
            "text": result.text,
            "parsed": parsed,
            "latency_s": self._latency_s(result.compute_time_ms),
            "profile": result.extra.get("profile"),
            "error": None,
        }

    def close(self):
        self._backend.close()

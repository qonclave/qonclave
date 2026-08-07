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
machine (and QONCLAVE_MOCK_INFERENCE unset) they return {"available":
False, ...} so the server can respond gracefully.

Mock fallback (QONCLAVE_MOCK_INFERENCE=1): when GenieX itself is
unavailable (no NPU, non-ARM64, geniex not installed) AND this flag is
set, reason()/structured_query() run against qonclave.inference.local.mock
.MockBackend instead of reporting unavailable -- a deterministic,
zero-hardware stand-in so the framework's HTTP surface, policies, and
dashboard are exercisable end-to-end on any machine. Opt-in, never
automatic: a real GenieX load failure on hardware that should support it
must still surface as "unavailable", not silently mock itself into looking
fine. Every dict this returns carries "mock": bool so a caller (and
/health) can always tell a canned response from a real one.
"""

from __future__ import annotations

import json
import logging
import os

from qonclave.core.enums import Complexity
from qonclave.inference.local.geniex import GenieXBackend
from qonclave.inference.local.mock import MockBackend

log = logging.getLogger("qonclave.vlm")

MOCK_INFERENCE_ENV = "QONCLAVE_MOCK_INFERENCE"

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
        self._mock: MockBackend | None = None

    def _resolve(self) -> tuple[object, bool]:
        """The backend inference actually runs against: GenieX when it's
        available, otherwise a lazily-created MockBackend IF
        QONCLAVE_MOCK_INFERENCE=1 -- opt-in, see the module docstring for
        why this is never automatic. Returns (backend, is_mock)."""
        if self._backend.available():
            return self._backend, False
        if os.environ.get(MOCK_INFERENCE_ENV, "0") == "1":
            if self._mock is None:
                self._mock = MockBackend(max_complexity=Complexity.VLM_REASON)
            return self._mock, True
        return self._backend, False

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        """True if the model is (or can be) loaded on this machine, OR the
        mock fallback is active."""
        backend, _ = self._resolve()
        return backend.available()

    def status(self) -> dict:
        # available() (not a bare "is it loaded" check) so the FIRST status
        # call - e.g. the dashboard's initial /user/events poll - triggers the
        # lazy load itself. Without this, a machine that never got -Warmup or
        # an explicit /user/reason call reports "unavailable" forever even
        # though the model loads fine on demand.
        backend, is_mock = self._resolve()
        if is_mock:
            return {
                "available": True,
                "model_id": "mock",
                "device_map": "mock",
                "arch": self._backend.status()["arch"],
                "load_attempted": True,
                "load_error": None,
                "mock": True,
            }
        s = backend.status()
        return {
            "available": s["available"],
            "model_id": s["model_id"],
            "device_map": s["device_map"],
            "arch": s["arch"],
            "load_attempted": s["load_attempted"],
            "load_error": s["load_error"],
            "mock": False,
        }

    def warmup(self) -> bool:
        """Eagerly load the model; returns True on success. Only ever
        attempts the real GenieX load -- the mock has nothing to warm up,
        and warmup() is used ahead of time specifically to surface a real
        load failure early, which resolving to mock here would hide."""
        self._backend.warmup()
        return self.is_available()

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
            "mock": False,
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
        backend, is_mock = self._resolve()
        if not backend.available():
            return self._unavailable(prompt)

        result = backend.infer(prompt=prompt, image_path=image_path,
                               max_tokens=max_new_tokens)
        if not result.ok:
            return {**self._unavailable(prompt), "available": True,
                    "error": f"reasoning failed: {result.error}", "mock": is_mock}

        preview = (result.text or "")[:200].replace("\n", " ")
        log.info("VLM reasoning done in %.2fs%s: %s",
                 (result.compute_time_ms or 0.0) / 1000.0,
                 " [mock]" if is_mock else "", preview)
        return {
            "available": True,
            "text": result.text,
            "prompt": prompt,
            "model_id": result.model_id,
            "latency_s": self._latency_s(result.compute_time_ms),
            "error": None,
            "profile": result.extra.get("profile"),
            "mock": is_mock,
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
        backend, is_mock = self._resolve()
        if not backend.available():
            u = self._unavailable(prompt)
            return {**u, "parsed": {}}

        result = backend.infer(prompt=prompt, image_path=image_path,
                               max_tokens=max_new_tokens, **gen_kwargs)
        if not result.ok:
            return {
                "available": True, "text": None, "parsed": {},
                "latency_s": None, "profile": None,
                "error": f"structured_query failed: {result.error}",
                "mock": is_mock,
            }

        # Mock text ("mock(<digest>)") is deliberately not JSON -- parsed
        # comes back {} every time, same as an unparseable real response,
        # rather than inventing a fake positive detection a caller might
        # mistake for a real one.
        parsed = extract_json(result.text or "")
        log.info("VLM structured_query (%.2fs)%s: parsed=%s",
                 (result.compute_time_ms or 0.0) / 1000.0,
                 " [mock]" if is_mock else "", parsed)
        if not parsed and result.text and not is_mock:
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
            "mock": is_mock,
        }

    def close(self):
        self._backend.close()

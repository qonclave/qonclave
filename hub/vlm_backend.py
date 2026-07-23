"""
vlm_backend.py — conditional vision-language reasoning for the Qonclave hub.

The heavy reasoning (GenieX + Qwen2.5-VL-7B) only runs on Snapdragon X laptops.
On every other machine (regular x86 Windows/Linux), the `geniex` import will
fail — so we import it LAZILY and never at module load time. That lets the rest
of the hub (HTTP server, upload handling, test webpage) run and be tested on any
laptop; only the reasoning call reports "unavailable".

Public API:
    backend = VLMBackend()          # cheap, does not import geniex
    backend.is_available()          # True only where geniex + model can load
    backend.warmup()                # optional: load the model up front
    result = backend.reason(image_path, prompt)   # -> dict

`reason()` always returns a dict and never raises for the caller; on an
unsupported machine it returns {"available": False, ...} so the server can
respond gracefully.
"""

from __future__ import annotations

import json
import logging
import platform
import threading
import time

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

# Structured verification: ask for a strict JSON object so we parse fields
# instead of keyword-matching prose. Used with json_mode=True.
VERIFY_PROMPT = (
    "You are a security camera verifier. Look at the image and respond with "
    "ONLY a JSON object, no other text, of exactly this form:\n"
    '{"person_present": true or false, '
    '"confidence": a number from 0 to 1, '
    '"description": "a short description of the scene"}\n'
    "Set person_present to true only if a human is clearly visible."
)
VERIFY_MAX_NEW_TOKENS = 128


def _extract_json(text: str) -> dict:
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
    # fall back to the first {...} span
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _is_arm64() -> bool:
    m = platform.machine().upper()
    return "ARM64" in m or "AARCH64" in m


class VLMBackend:
    """Lazily loads the GenieX VLM. Safe to construct on any machine."""

    def __init__(self, model_id: str = MODEL_ID, device_map: str = DEVICE_MAP):
        self.model_id = model_id
        self.device_map = device_map
        self._model = None
        self._load_error: str | None = None
        self._load_attempted = False
        self._lock = threading.Lock()  # generation is serialized; model isn't reentrant

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        """True only if the model is (or can be) loaded on this machine."""
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        return self._try_load()

    def status(self) -> dict:
        return {
            "available": self._model is not None,
            "model_id": self.model_id,
            "device_map": self.device_map,
            "arch": platform.machine(),
            "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    def warmup(self) -> bool:
        """Eagerly load the model; returns True on success."""
        return self._try_load()

    # --- internal -----------------------------------------------------------
    def _try_load(self) -> bool:
        with self._lock:
            if self._model is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True

            if not _is_arm64():
                self._load_error = (
                    f"non-ARM64 host ({platform.machine()}); GenieX reasoning is "
                    "Snapdragon-only. Server runs, reasoning disabled."
                )
                log.warning("VLM unavailable: %s", self._load_error)
                return False

            try:
                # Imported HERE, never at module top, so non-Snapdragon machines
                # can still import this module and run the rest of the hub.
                from geniex import AutoModelForCausalLM  # type: ignore
            except Exception as e:  # ImportError or SDK/runtime load failure
                self._load_error = f"could not import geniex: {e}"
                log.warning("VLM unavailable: %s", self._load_error)
                return False

            try:
                log.info("Loading VLM model '%s' (device_map=%s)...",
                         self.model_id, self.device_map)
                t0 = time.time()
                self._model = AutoModelForCausalLM.from_pretrained(
                    self.model_id, device_map=self.device_map,
                )
                log.info("VLM model loaded in %.1fs", time.time() - t0)
                return True
            except Exception as e:
                self._load_error = f"model load failed: {e}"
                log.error("VLM load failed: %s", self._load_error)
                self._model = None
                return False

    # --- inference ----------------------------------------------------------
    def _generate(self, image_path: str, prompt: str, max_new_tokens: int,
                  **gen_kwargs) -> dict:
        """
        Shared generation core. Returns the raw result dict. Assumes the model
        is loaded and the lock is held by the caller. gen_kwargs (temperature,
        json_mode, grammar, stop, …) are passed to model.generate(); if the SDK
        build rejects one, we retry without the extras so we degrade instead of
        crashing.
        """
        # GenieX keeps conversation state + KV cache across generate() calls.
        # Each verify/reason must be an independent single-turn inference, so
        # clear the prior turn first — otherwise the previous image's state
        # bleeds into this one and the same frame can classify differently
        # ("VLM history shrank ... without reset()" warning). Guarded for SDK
        # builds that lack reset().
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception as e:
                log.debug("model.reset() failed (continuing): %s", e)

        t0 = time.time()
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image_path},
                {"type": "text", "text": prompt},
            ],
        }]
        chat_prompt = self._model.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        try:
            output = self._model.generate(
                chat_prompt, images=[image_path],
                max_new_tokens=max_new_tokens, **gen_kwargs,
            )
        except TypeError as e:
            # Older SDK build without json_mode/grammar/etc. — retry plain.
            if gen_kwargs:
                log.warning("generate() rejected %s (%s); retrying without extras",
                            list(gen_kwargs), e)
                output = self._model.generate(
                    chat_prompt, images=[image_path], max_new_tokens=max_new_tokens,
                )
            else:
                raise
        latency = time.time() - t0

        profile = None
        prof = getattr(output, "profile", None)
        if prof is not None:
            profile = {
                "generated_tokens": getattr(prof, "generated_tokens", None),
                "decode_speed": getattr(prof, "decode_speed", None),
                "stop_reason": getattr(prof, "stop_reason", None),
            }
        text = getattr(output, "text", str(output))
        return {
            "available": True,
            "text": text,
            "prompt": prompt,
            "model_id": self.model_id,
            "latency_s": round(latency, 3),
            "error": None,
            "profile": profile,
        }

    def _unavailable(self, prompt: str) -> dict:
        return {
            "available": False,
            "text": None,
            "prompt": prompt,
            "model_id": self.model_id,
            "latency_s": None,
            "error": self._load_error or "VLM not available on this machine",
            "profile": None,
        }

    def reason(self, image_path: str, prompt: str | None = None,
               max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS) -> dict:
        """
        Free-form reasoning over one image (used by the /user/reason tester).
        Always returns a dict; never raises for the caller.
        """
        prompt = prompt or DEFAULT_PROMPT
        if not self.is_available():
            return self._unavailable(prompt)
        with self._lock:
            try:
                result = self._generate(image_path, prompt, max_new_tokens)
                preview = (result.get("text") or "")[:200].replace("\n", " ")
                log.info("VLM reasoning done in %.2fs: %s",
                         result.get("latency_s") or 0.0, preview)
                return result
            except Exception as e:
                log.exception("VLM reasoning failed")
                return {**self._unavailable(prompt), "available": True,
                        "error": f"reasoning failed: {e}"}

    def verify(self, image_path: str) -> dict:
        """
        Structured verification for the edge contract. Asks the VLM for a strict
        JSON object and parses it — no brittle keyword matching on prose.

        Returns (always, never raises for caller):
            {"available": bool,
             "person_present": bool|None,
             "confidence": float|None,     # 0..1
             "description": str|None,
             "alert": str|None,
             "raw_text": str|None,
             "latency_s": float|None,
             "profile": {...}|None,
             "error": str|None}
        """
        if not self.is_available():
            u = self._unavailable(VERIFY_PROMPT)
            return {
                "available": False, "person_present": None, "confidence": None,
                "description": None, "alert": None, "raw_text": None,
                "latency_s": None, "profile": None, "error": u["error"],
            }

        with self._lock:
            try:
                # json_mode + low temperature => deterministic, parseable output.
                result = self._generate(
                    image_path, VERIFY_PROMPT, VERIFY_MAX_NEW_TOKENS,
                    json_mode=True, temperature=0.1,
                )
            except Exception as e:
                log.exception("VLM verify failed")
                return {
                    "available": True, "person_present": None, "confidence": None,
                    "description": None, "alert": None, "raw_text": None,
                    "latency_s": None, "profile": None,
                    "error": f"verify failed: {e}",
                }

        parsed = _extract_json(result.get("text") or "")
        person = parsed.get("person_present")
        if isinstance(person, str):
            person = person.strip().lower() in ("true", "yes", "1")
        conf = parsed.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        desc = parsed.get("description")

        if person is True:
            alert = "Person verified near camera"
        elif person is False:
            alert = "No person confirmed in frame"
        else:
            alert = "Verification inconclusive"

        log.info("VLM verify (%.2fs): person=%s conf=%s",
                 result.get("latency_s") or 0.0, person, conf)
        return {
            "available": True,
            "person_present": person,
            "confidence": conf,
            "description": desc,
            "alert": alert,
            "raw_text": result.get("text"),
            "latency_s": result.get("latency_s"),
            "profile": result.get("profile"),
            "error": None,
        }

    def close(self):
        with self._lock:
            if self._model is not None:
                try:
                    self._model.close()
                except Exception:
                    pass
                self._model = None

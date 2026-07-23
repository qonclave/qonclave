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
    def reason(self, image_path: str, prompt: str | None = None,
               max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS) -> dict:
        """
        Run the VLM on one image. Always returns a dict; never raises for the
        caller. Shape:
            {"available": bool, "text": str|None, "prompt": str,
             "model_id": str, "latency_s": float|None, "error": str|None,
             "profile": {...}|None}
        """
        prompt = prompt or DEFAULT_PROMPT

        if not self.is_available():
            return {
                "available": False,
                "text": None,
                "prompt": prompt,
                "model_id": self.model_id,
                "latency_s": None,
                "error": self._load_error or "VLM not available on this machine",
                "profile": None,
            }

        with self._lock:
            try:
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
                output = self._model.generate(
                    chat_prompt, images=[image_path], max_new_tokens=max_new_tokens,
                )
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
                log.info("VLM reasoning done in %.2fs (%s tok)",
                         latency,
                         profile.get("generated_tokens") if profile else "?")
                return {
                    "available": True,
                    "text": text,
                    "prompt": prompt,
                    "model_id": self.model_id,
                    "latency_s": round(latency, 3),
                    "error": None,
                    "profile": profile,
                }
            except Exception as e:
                log.exception("VLM reasoning failed")
                return {
                    "available": True,
                    "text": None,
                    "prompt": prompt,
                    "model_id": self.model_id,
                    "latency_s": None,
                    "error": f"reasoning failed: {e}",
                    "profile": None,
                }

    def close(self):
        with self._lock:
            if self._model is not None:
                try:
                    self._model.close()
                except Exception:
                    pass
                self._model = None

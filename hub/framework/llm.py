"""
llm.py — conditional text-only LLM reasoning for the Qonclave framework.

Uses the same lazy-load, ARM64-guard, and best-effort philosophy as vlm.py.
The heavy model (GenieX + Qwen3-4B) is imported and loaded only on demand,
only on ARM64, and never at module load time — so the rest of the hub runs
on any machine; on x86 the backend reports "unavailable" and returns a safe
stub.

Unlike VLMBackend this backend accepts no image — it is text-in, text-out,
suitable for reasoning over structured text such as SMS replies, event
summaries, or operator instructions.

Public API:
    backend = LLMBackend()              # cheap, does not import geniex
    backend.is_available()              # True only where geniex + model can load
    backend.warmup()                    # optional: load the model up front
    result = backend.generate(prompt)   # -> dict
    result = backend.generate(          # with optional system prompt
        prompt, system="You are ...",
        max_new_tokens=256,
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
import platform
import threading
import time

log = logging.getLogger("qonclave.llm")

MODEL_ID = "ai-hub-models/Qwen3-4B"
DEVICE_MAP = "qairt"
DEFAULT_MAX_NEW_TOKENS = 512


def _is_arm64() -> bool:
    m = platform.machine().upper()
    return "ARM64" in m or "AARCH64" in m


class LLMBackend:
    """Lazily loads the GenieX text-only LLM. Safe to construct on any machine."""

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
                    "Snapdragon-only. Server runs, LLM reasoning disabled."
                )
                log.warning("LLM unavailable: %s", self._load_error)
                return False

            try:
                from geniex import AutoModelForCausalLM  # type: ignore
            except Exception as e:
                self._load_error = f"could not import geniex: {e}"
                log.warning("LLM unavailable: %s", self._load_error)
                return False

            try:
                log.info("Loading LLM model '%s' (device_map=%s)...",
                         self.model_id, self.device_map)
                t0 = time.time()
                self._model = AutoModelForCausalLM.from_pretrained(
                    self.model_id, device_map=self.device_map,
                )
                log.info("LLM model loaded in %.1fs", time.time() - t0)
                return True
            except Exception as e:
                self._load_error = f"model load failed: {e}"
                log.error("LLM load failed: %s", self._load_error)
                self._model = None
                return False

    def _unavailable(self) -> dict:
        return {
            "available": False,
            "text": None,
            "model_id": self.model_id,
            "latency_s": None,
            "error": self._load_error or "LLM not available on this machine",
            "profile": None,
        }

    # --- inference ----------------------------------------------------------

    def generate(self, prompt: str, system: str | None = None,
                 max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS) -> dict:
        """
        Text-in, text-out generation. The optional system prompt sets the
        model's role / persona for the conversation.

        Always returns a dict and never raises for the caller; on an
        unsupported machine returns {"available": False, ...}.
        """
        if not self.is_available():
            return self._unavailable()

        with self._lock:
            try:
                reset = getattr(self._model, "reset", None)
                if callable(reset):
                    try:
                        reset()
                    except Exception as e:
                        log.debug("model.reset() failed (continuing): %s", e)

                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                chat_prompt = self._model.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True,
                )

                t0 = time.time()
                output = self._model.generate(chat_prompt, max_new_tokens=max_new_tokens)
                latency = time.time() - t0

                text = getattr(output, "text", str(output))
                profile = None
                prof = getattr(output, "profile", None)
                if prof is not None:
                    profile = {
                        "generated_tokens": getattr(prof, "generated_tokens", None),
                        "decode_speed": getattr(prof, "decode_speed", None),
                        "stop_reason": getattr(prof, "stop_reason", None),
                    }

                preview = (text or "")[:120].replace("\n", " ")
                log.info("LLM generate (%.2fs): %s", latency, preview)
                return {
                    "available": True,
                    "text": text,
                    "model_id": self.model_id,
                    "latency_s": round(latency, 3),
                    "error": None,
                    "profile": profile,
                }
            except Exception as e:
                log.exception("LLM generate failed")
                return {
                    **self._unavailable(),
                    "available": True,
                    "error": f"generate failed: {e}",
                }

    def close(self):
        with self._lock:
            if self._model is not None:
                try:
                    self._model.close()
                except Exception:
                    pass
                self._model = None

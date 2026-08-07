"""
geniex.py -- GenieX / Qwen2.5-VL backend for Snapdragon X.

Imported lazily and only on ARM64, so a hub on any other machine still serves every route and
reports inference unavailable rather than failing to start.

One class serves both roles GenieX itself does: text-only (Qwen3-4B-class models) and
vision-language (Qwen2.5-VL-class models) are the same `infer()` call, distinguished only by
whether an image is supplied. A caller wanting one role loads one model_id; nothing here assumes
which.

Origin: hub/framework/vlm.py and hub/framework/llm.py, which now wrap this class to keep their
existing dict-shaped reason()/structured_query()/generate() APIs for their current callers
(apps/security/policy.py, apps/assistant/routes.py) rather than switching every caller to
ModelBackend's InferResult in the same change -- see CONVENTIONS.md.
"""

from __future__ import annotations

import base64
import logging
import os
import platform
import tempfile
import threading
import time
from typing import Any

from ...core.enums import Complexity, TaskStatus
from ...core.models import MediaPayload
from ..backend import InferResult, ModelBackend

log = logging.getLogger("qonclave.inference.geniex")


def _is_arm64() -> bool:
    m = platform.machine().upper()
    return "ARM64" in m or "AARCH64" in m


def _payload_image_path(payloads: list[MediaPayload] | None) -> tuple[str | None, bool]:
    """First image payload written to a temp file GenieX can open, or (None, False) if there
    isn't one. The bool says whether the caller must clean the file up -- False for a caller-
    supplied image_path passed straight through, which this function never sees."""
    for p in payloads or []:
        if p.media_type.startswith("image/") and p.data_encoding == "base64":
            try:
                raw = base64.b64decode(p.data)
            except Exception:
                continue
            ext = ".jpg" if "jpeg" in p.media_type else ".png"
            fd, path = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return path, True
    return None, False


class GenieXBackend(ModelBackend):
    """Lazily loads one GenieX model. Safe to construct on any machine -- only the first
    infer()/warmup()/available() call attempts the ARM64-gated import and load."""

    name = "geniex"

    def __init__(self, model_id: str, device_map: str = "qairt",
                max_complexity: Complexity = Complexity.LLM_REASON):
        self.model_id = model_id
        self.device_map = device_map
        self.max_complexity = max_complexity
        self._model = None
        self._load_error: str | None = None
        self._load_attempted = False
        self._lock = threading.Lock()  # generation is serialized; the model isn't reentrant
        # None until the first thinking=False call tells us whether this tokenizer's chat
        # template accepts enable_thinking.
        self._thinking_kwarg_ok: bool | None = None

    # --- ModelBackend -----------------------------------------------------------
    def available(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        return self._try_load()

    def warmup(self) -> None:
        self._try_load()

    def status(self) -> dict[str, Any]:
        # available() (not a bare self._model check) so the first status call triggers the lazy
        # load itself -- a caller that never warmed up explicitly still reports accurately.
        available = self.available()
        return {
            "name": self.name, "available": available,
            "max_complexity": self.max_complexity.wire,
            "model_id": self.model_id, "device_map": self.device_map,
            "arch": platform.machine(), "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        if self._model is None:
            return
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception as e:
                log.debug("model.reset() failed (continuing): %s", e)

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
                    "Snapdragon-only."
                )
                log.warning("GenieX unavailable: %s", self._load_error)
                return False

            try:
                # Imported HERE, never at module top, so non-Snapdragon machines can still
                # import this module and construct this class.
                from geniex import AutoModelForCausalLM  # type: ignore
            except Exception as e:
                self._load_error = f"could not import geniex: {e}"
                log.warning("GenieX unavailable: %s", self._load_error)
                return False

            try:
                log.info("Loading GenieX model '%s' (device_map=%s)...",
                         self.model_id, self.device_map)
                t0 = time.time()
                self._model = AutoModelForCausalLM.from_pretrained(
                    self.model_id, device_map=self.device_map,
                )
                log.info("GenieX model loaded in %.1fs", time.time() - t0)
                return True
            except Exception as e:
                self._load_error = f"model load failed: {e}"
                log.error("GenieX load failed: %s", self._load_error)
                self._model = None
                return False

    def _apply_chat_template(self, messages: list[dict], thinking: bool) -> str:
        """Render the chat template, suppressing a hybrid-reasoning model's <think> block when
        thinking is False. enable_thinking is a Qwen3 chat-template kwarg; tokenizers that
        predate it raise TypeError -- probed once, then remembered, so a hot path doesn't retry
        (or re-warn) on every call."""
        tokenizer = self._model.tokenizer
        kwargs = {"tokenize": False, "add_generation_prompt": True}

        if not thinking and self._thinking_kwarg_ok is not False:
            try:
                chat_prompt = tokenizer.apply_chat_template(
                    messages, enable_thinking=False, **kwargs,
                )
                self._thinking_kwarg_ok = True
                return chat_prompt
            except TypeError as e:
                self._thinking_kwarg_ok = False
                log.warning(
                    "tokenizer does not accept enable_thinking (%s); replies may "
                    "contain a <think> block", e,
                )

        return tokenizer.apply_chat_template(messages, **kwargs)

    # --- ModelBackend.infer() -------------------------------------------------
    def infer(
        self,
        *,
        prompt: str | None = None,
        payloads: list[MediaPayload] | None = None,
        image_path: str | None = None,
        model_id: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.1,
        json_mode: bool = False,
        timeout_s: float | None = None,
        system: str | None = None,
        thinking: bool = True,
    ) -> InferResult:
        """`image_path` (or the first image/* payload in `payloads`) makes this a vision-language
        call; omitting both makes it text-only. `system`/`thinking`/`temperature`/`json_mode` are
        passed to `model.generate()`; an older SDK build that rejects one gets retried without the
        extras rather than failing the call. `system`/`thinking` are GenieX-specific, beyond
        ModelBackend's base signature -- kwargs, so a generic caller going through
        `qonclave.placement`/`inference.resolve()` never needs to know about them.
        """
        if not self.available():
            return InferResult.unavailable(
                self._load_error or "GenieX not available on this machine")

        resolved_image, owns_temp_file = image_path, False
        if resolved_image is None:
            resolved_image, owns_temp_file = _payload_image_path(payloads)

        with self._lock:
            try:
                self._reset_locked()  # GenieX keeps conversation state across generate() calls;
                # each infer() must be an independent single-turn call, or the previous
                # image/prompt's state bleeds into this one.

                content: list[dict] = []
                if resolved_image is not None:
                    content.append({"type": "image", "image": resolved_image})
                content.append({"type": "text", "text": prompt or ""})
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": content})

                chat_prompt = self._apply_chat_template(messages, thinking)

                gen_kwargs: dict[str, Any] = {"temperature": temperature}
                if json_mode:
                    gen_kwargs["json_mode"] = True

                t0 = time.time()
                gen = {"max_new_tokens": max_tokens, **gen_kwargs}
                if resolved_image is not None:
                    gen["images"] = [resolved_image]
                try:
                    output = self._model.generate(chat_prompt, **gen)
                except TypeError as e:
                    # Older SDK build without json_mode/temperature/etc. -- retry plain.
                    log.warning("generate() rejected %s (%s); retrying without extras",
                               list(gen_kwargs), e)
                    plain = {"max_new_tokens": max_tokens}
                    if resolved_image is not None:
                        plain["images"] = [resolved_image]
                    output = self._model.generate(chat_prompt, **plain)
                latency_ms = (time.time() - t0) * 1000.0

                profile: dict[str, Any] = {}
                prof = getattr(output, "profile", None)
                if prof is not None:
                    profile = {
                        "generated_tokens": getattr(prof, "generated_tokens", None),
                        "decode_speed": getattr(prof, "decode_speed", None),
                        "stop_reason": getattr(prof, "stop_reason", None),
                    }
                text = getattr(output, "text", str(output))

                preview = (text or "")[:200].replace("\n", " ")
                log.info("GenieX infer done in %.0fms: %s", latency_ms, preview)

                return InferResult(
                    status=TaskStatus.OK,
                    text=text,
                    model_id=model_id or self.model_id,
                    node_id=self.name,
                    compute_time_ms=latency_ms,
                    extra={"profile": profile} if profile else {},
                )
            except Exception as e:
                log.exception("GenieX infer failed")
                return InferResult(status=TaskStatus.ERROR, error=f"infer failed: {e}",
                                   model_id=model_id or self.model_id, node_id=self.name)
            finally:
                if owns_temp_file and resolved_image is not None:
                    try:
                        os.remove(resolved_image)
                    except OSError:
                        pass

    def close(self):
        with self._lock:
            if self._model is not None:
                try:
                    self._model.close()
                except Exception:
                    pass
                self._model = None

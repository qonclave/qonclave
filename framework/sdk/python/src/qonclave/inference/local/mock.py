"""
mock.py — a deterministic backend for tests, CI, and machines with no accelerator.

This is what makes the framework's "runs anywhere" property testable. The reference hub already
behaves this way — on a non-Snapdragon laptop every route works and inference reports itself
unavailable — and MockBackend turns that from a fallback into something a test can assert on.

Deterministic by construction: the same prompt yields the same answer, so a conformance or
placement test can assert on output without a model.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from ...core.enums import Complexity, TaskStatus
from ...core.models import MediaPayload
from ..backend import InferResult, ModelBackend


class MockBackend(ModelBackend):
    """Answers plausibly, instantly, and identically every time."""

    name = "mock"

    def __init__(
        self,
        *,
        max_complexity: Complexity = Complexity.LLM_REASON,
        latency_ms: float = 0.0,
        available: bool = True,
    ) -> None:
        self.max_complexity = max_complexity
        self._latency_ms = latency_ms
        self._available = available
        self.calls: list[dict[str, Any]] = []
        self.resets = 0

    def available(self) -> bool:
        return self._available

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
    ) -> InferResult:
        self.calls.append({"prompt": prompt, "model_id": model_id, "json_mode": json_mode})

        if not self._available:
            return InferResult.unavailable("mock backend disabled")

        # Refuse work we cannot finish rather than starting it. On a battery-powered node the
        # difference between "declined" and "started then discarded" is energy.
        if timeout_s is not None and self._latency_ms / 1000.0 > timeout_s:
            return InferResult(
                status=TaskStatus.DEADLINE_EXCEEDED,
                error=f"needs {self._latency_ms}ms, budget was {timeout_s * 1000:.0f}ms",
                node_id=self.name,
            )

        started = time.monotonic()
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000.0)

        seed = hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()
        confidence = 0.5 + (int(seed[:2], 16) / 512.0)

        result: dict[str, Any] = {
            "mock": True,
            "confidence": round(confidence, 4),
            "digest": seed[:12],
        }

        return InferResult(
            status=TaskStatus.OK,
            text=f"mock({seed[:12]})",
            data=result if json_mode else None,
            model_id=model_id or "mock-1",
            node_id=self.name,
            compute_time_ms=(time.monotonic() - started) * 1000.0,
        )

    def reset(self) -> None:
        self.resets += 1

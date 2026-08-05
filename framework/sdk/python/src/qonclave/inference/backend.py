"""
backend.py — the inference contract.

This ABC lives in a shared layer, NOT in `qonclave.compute`, and that placement is deliberate.

A hub is allowed to run models itself; Compute is an optional role. If `ModelBackend` lived in the
compute package, every hub doing local VLM work would have to `import qonclave.compute` — which
would break the role-isolation rule in CONVENTIONS.md and make "Compute is optional" false in
practice. Putting the contract here means:

    qonclave.inference   the capability      (local backends AND the remote client)
    qonclave.compute     an optional server  (exposes a backend over the network)

so a node calls `inference.resolve(task)` and gets back either a local backend or a RemoteBackend,
with the same interface, and never learns which kind of deployment it is in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.enums import Complexity, TaskStatus
from ..core.models import MediaPayload


@dataclass(slots=True)
class InferResult:
    status: TaskStatus
    text: str | None = None
    data: dict[str, Any] | None = None
    """Parsed output when the request asked for structured results."""

    model_id: str | None = None
    node_id: str | None = None
    compute_time_ms: float | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is TaskStatus.OK

    @classmethod
    def unavailable(cls, why: str) -> "InferResult":
        """A backend that cannot run right now.

        Returned rather than raised, matching the existing framework's "runs anywhere" behavior:
        a hub on a non-Snapdragon laptop still serves every route, and inference reports itself
        unavailable instead of taking the process down.
        """
        return cls(status=TaskStatus.ERROR, error=why)


class ModelBackend(ABC):
    """Something that can run a model.

    Implemented by local backends (GenieX, ONNX, mock) and by RemoteBackend, which forwards to a
    compute node. Callers cannot tell the difference, and must not try.
    """

    name: str = "backend"
    max_complexity: Complexity = Complexity.HEURISTIC

    @abstractmethod
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
        """Run one inference.

        `timeout_s` comes from the placement deadline. A backend that cannot finish within it
        SHOULD return `TaskStatus.DEADLINE_EXCEEDED` without starting, rather than starting work
        it knows will be discarded — on a battery-powered node that distinction is energy.
        """
        raise NotImplementedError

    def available(self) -> bool:
        """Whether this backend can currently serve requests."""
        return True

    def warmup(self) -> None:
        """Optionally preload. Callers must not depend on this having been called."""

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "max_complexity": self.max_complexity.wire}

    def reset(self) -> None:
        """Discard all state from the previous inference.

        For a shared compute node this is the zero-leakage guarantee in SECURITY.md §2, not an
        optimization: model context, prompts, and intermediate tensors must not survive from one
        tenant's request into the next.
        """

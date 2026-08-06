"""
task.py — one unit of inference work as it moves through the ladder.

`InferenceTask` is the runtime object; `TaskDescriptor` (core.models) is the part that goes on the
wire when a task is escalated to another node. The split matters: the descriptor is the
application's declared intent and must survive the hop, while the payload and callbacks are local.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.enums import Complexity, Privacy, Urgency
from ..core.models import MediaPayload, TaskDescriptor


@dataclass(slots=True)
class InferenceTask:
    """A request to run a model somewhere.

    Construct with `InferenceTask.declare(...)` rather than filling the descriptor by hand — the
    declaration is the part a developer is expected to think about, and keeping it to one call
    makes the declared-vs-measured boundary obvious at the call site.
    """

    task_id: str
    model_id: str | None = None
    prompt: str | None = None
    payloads: list[MediaPayload] = field(default_factory=list)
    image_path: str | None = None

    descriptor: TaskDescriptor = field(default_factory=TaskDescriptor)
    tenant_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def declare(
        cls,
        task_id: str,
        *,
        complexity: Complexity = Complexity.HEURISTIC,
        urgency: Urgency = Urgency.NORMAL,
        privacy: Privacy = Privacy.UNRESTRICTED,
        use_case: str | None = None,
        deadline_ms: int | None = None,
        **kwargs: Any,
    ) -> "InferenceTask":
        """Declare a task's intent. Everything here is the application's to state; nothing here is
        measured."""
        return cls(
            task_id=task_id,
            descriptor=TaskDescriptor(
                complexity=complexity,
                urgency=urgency,
                privacy=privacy,
                use_case=use_case,
                deadline_ms=deadline_ms,
                remaining_ms=deadline_ms,
            ),
            **kwargs,
        )

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def budget_left_ms(self) -> int | None:
        """Budget remaining right now, accounting for time already spent locally.

        Distinct from `descriptor.remaining_ms`, which is only updated at hop boundaries. This is
        the value to check before starting work that will take a known amount of time.
        """
        if self.descriptor.remaining_ms is None:
            return None
        return max(0, self.descriptor.remaining_ms - self.elapsed_ms)

    def for_escalation(self, hop: str) -> TaskDescriptor:
        """The descriptor to put on the wire when handing this task to the next tier.

        Deducts what has been spent and records the hop. Callers must use this rather than sending
        `self.descriptor` directly, or the receiving tier will plan against a budget that is
        already gone.
        """
        spent = self.descriptor.spend(self.elapsed_ms)
        return spent.model_copy(update={"hops": [*spent.hops, hop]})

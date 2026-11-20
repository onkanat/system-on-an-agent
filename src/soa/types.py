"""Shared message and result types used on the bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """A unit of work routed by the bus."""

    channel: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "core"


@dataclass
class Result:
    """Outcome of a bus dispatch or a plan step."""

    ok: bool
    data: Any = None
    error: str | None = None
    action: str | None = None
    # Set when the step failed by raising: the bus turns the exception into a
    # failed Result instead of letting it escape, and keeps it here so callers
    # (the run loop, add-on hooks) can still inspect what actually went wrong.
    exception: BaseException | None = None

    def text(self) -> str:
        """Human-readable form of the result."""
        if not self.ok:
            return self.error or "error"
        if self.data is None:
            return "ok"
        return str(self.data)

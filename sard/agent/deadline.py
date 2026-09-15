"""Hierarchical deadline + cancellation primitives (workstream E).

A ``Deadline`` is a monotonic end-time plus a protected ``reserve`` slice.
Stages must stop starting new work once ``remaining - reserve <= 0`` so the
reserve is preserved for terminal work (artifacts + delta + done + flush on
chat; verify + store + serialize on itinerary).

An internal hard stop of ``request - 2s`` guarantees ``done`` beats the
platform kill. The chat-text no-artifact fast path is unaffected (no
orchestration runs, so no reserve is consumed).

Ownership: workstream E (runtime). Additive only — callers keep float
compat via :func:`coerce_deadline`.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Union

__all__ = [
    "DeadlineTimeoutError",
    "DeadlineCancelledError",
    "Deadline",
    "coerce_deadline",
    "deadline_from_timeout",
    "rag_overall_deadline_s",
    "chat_request_budget",
    "itinerary_request_budget",
]


class DeadlineTimeoutError(TimeoutError):
    """Typed timeout raised by :meth:`Deadline.check`.

    Subclasses builtin ``TimeoutError`` (== ``asyncio.TimeoutError`` on
    3.11+) so existing ``except TimeoutError`` / ``"timeout" in str`` /
    ``"deadline" in str`` guards keep working. Carries ``stage`` and
    ``error_category="timeout"`` for typed responses.
    """

    def __init__(self, message: str = "deadline exceeded", stage: str = ""):
        super().__init__(message)
        self.stage = stage
        self.error_category = "timeout"


class DeadlineCancelledError(Exception):
    """Typed cancellation raised by :meth:`Deadline.check`.

    Deliberately an ``Exception`` (not ``asyncio.CancelledError`` /
    ``BaseException``) so generic ``except Exception`` fallback paths can
    observe it, while server code maps it to typed 499 / SSE
    ``cancelled``. Carries ``error_category="cancelled"``.
    """

    def __init__(self, message: str = "cancelled", stage: str = ""):
        super().__init__(message)
        self.stage = stage
        self.error_category = "cancelled"


@dataclass
class Deadline:
    """Monotonic end-time plus protected reserve plus optional cancel flag.

    ``monotonic_end`` is a ``time.monotonic()`` timestamp for the whole
    request. ``reserve_s`` is protected for terminal work and must not be
    consumed by new candidates/renders.
    """

    monotonic_end: float
    reserve_s: float = 0.0
    cancel_event: Optional[threading.Event] = field(default=None, repr=False)
    label: str = ""

    def remaining(self) -> float:
        return float(self.monotonic_end - time.monotonic())

    def reserve_remaining(self) -> float:
        """Budget left for *new* work; reserve is preserved."""
        return float(self.monotonic_end - time.monotonic() - (self.reserve_s or 0.0))

    def is_cancelled(self) -> bool:
        try:
            return bool(self.cancel_event is not None and self.cancel_event.is_set())
        except Exception:
            return False

    def is_expired(self) -> bool:
        """True when no budget for new work remains (reserve preserved)."""
        return self.reserve_remaining() <= 0

    def is_hard_expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, stage: str = "") -> None:
        """Raise typed Cancelled/Timeout before starting new work."""
        if self.is_cancelled():
            raise DeadlineCancelledError(f"cancelled at stage '{stage or self.label}'", stage=stage or self.label)
        if self.reserve_remaining() <= 0:
            raise DeadlineTimeoutError(
                f"deadline exceeded at stage '{stage or self.label}' "
                f"(remaining={self.remaining():.2f}s reserve={self.reserve_s:.1f}s)",
                stage=stage or self.label,
            )

    def check_hard(self, stage: str = "") -> None:
        """Raise on absolute expiry (ignores reserve). For flush paths."""
        if self.is_cancelled():
            raise DeadlineCancelledError(f"cancelled at stage '{stage or self.label}'", stage=stage or self.label)
        if self.remaining() <= 0:
            raise DeadlineTimeoutError(
                f"hard deadline exceeded at stage '{stage or self.label}'",
                stage=stage or self.label,
            )

    def time_left_for_attempt(self, attempts_left: int = 1, cap_s: float = 30.0) -> float:
        """Per-attempt budget: ``min(cap, remaining / attempts_left)``."""
        try:
            left = max(1, int(attempts_left))
        except Exception:
            left = 1
        return max(0.0, min(float(cap_s), self.remaining() / left))


DeadlineLike = Union["Deadline", float, int, None]


def coerce_deadline(
    value: DeadlineLike,
    reserve_s: float = 0.0,
    cancel_event: Optional[threading.Event] = None,
    label: str = "",
) -> Optional[Deadline]:
    """Float-compat shim: float/int = absolute ``monotonic_end``.

    ``None`` stays ``None`` (caller decides defaults). A ``Deadline``
    instance is returned as-is (missing cancel/label filled in when the
    caller supplies them and the instance lacks them).
    """
    if value is None:
        return None
    if isinstance(value, Deadline):
        if cancel_event is not None and value.cancel_event is None:
            value.cancel_event = cancel_event
        if label and not value.label:
            value.label = label
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return Deadline(
            monotonic_end=float(value),
            reserve_s=float(reserve_s or 0.0),
            cancel_event=cancel_event,
            label=label,
        )
    return None


def deadline_from_timeout(
    timeout_s: float,
    reserve_s: float = 0.0,
    cancel_event: Optional[threading.Event] = None,
    label: str = "",
) -> Deadline:
    return Deadline(
        monotonic_end=time.monotonic() + max(0.0, float(timeout_s)),
        reserve_s=float(reserve_s or 0.0),
        cancel_event=cancel_event,
        label=label,
    )


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def rag_overall_deadline_s(default: float = 12.0) -> float:
    """Outer RAG budget. New env ``SARD_RAG_OVERALL_DEADLINE`` (default 12s).

    The itinerary retrieval slice passes ``default=15`` explicitly.
    """
    return _env_float("SARD_RAG_OVERALL_DEADLINE", default)


def chat_request_budget() -> tuple[float, float]:
    """(overall_s, reserve_s) for chat: 35s request -> 25s pipeline + 10s."""
    try:
        overall = float((os.environ.get("SARD_CHAT_OVERALL_TIMEOUT", "35") or "35").strip())
    except ValueError:
        overall = 35.0
    overall = max(5.0, min(60.0, overall))
    return overall, 10.0


def itinerary_request_budget() -> tuple[float, float]:
    """(overall_s, reserve_s) for itinerary: 40s request -> 32s + 8s."""
    try:
        overall = float((os.environ.get("SARD_ITINERARY_TIMEOUT", "40") or "40").strip())
    except ValueError:
        overall = 40.0
    overall = max(30.0, min(45.0, overall))
    return overall, 8.0


# Re-export for asyncio-compat checks elsewhere.
CancelledBase = (DeadlineCancelledError, asyncio.CancelledError)
TimeoutBase = (DeadlineTimeoutError, TimeoutError)

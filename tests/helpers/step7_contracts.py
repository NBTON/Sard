"""Import shim for the Step 7 public UI contracts.

Prefers the real ``sard.application.contracts`` module (Agent A, lands later);
falls back to the frozen replica in ``tests.helpers._frozen_contracts`` so
tests are executable today and automatically exercise the real module once it
exists.  ``CONTRACTS_SOURCE`` records which one is active.
"""

from __future__ import annotations

try:
    from sard.application.contracts import (  # type: ignore[import-untyped]
        CalendarAfterDateRequest,
        UIArtifactView,
        UIExecutionMode,
        UIModeKind,
        UIModeStatus,
        UIModelRoute,
        UIProgressEvent,
        UIProgressState,
        UIRunRequest,
        UIRunResult,
        UIStage,
        UISourceView,
    )

    CONTRACTS_SOURCE = "sard.application.contracts"
except ImportError:
    from tests.helpers._frozen_contracts import (  # noqa: F401
        CalendarAfterDateRequest,
        UIArtifactView,
        UIExecutionMode,
        UIModeKind,
        UIModeStatus,
        UIModelRoute,
        UIProgressEvent,
        UIProgressState,
        UIRunRequest,
        UIRunResult,
        UIStage,
        UISourceView,
    )

    CONTRACTS_SOURCE = "tests.helpers._frozen_contracts"

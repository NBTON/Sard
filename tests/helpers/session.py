"""Reference model of the Step 7 Streamlit session-state contract.

Streamlit reruns are the largest functional conflict risk (frozen contract,
"Parallel boundaries and conflict risks").  This test-side model encodes the
stable session keys and the rerun rules without requiring a live Streamlit
runtime: the service instance, current request/run ID, progress list, final
result, date inputs, retry token and demo flag live in session state; buttons
set intent; only a new explicit run token may call ``stream_run()``.

Integration should mirror these keys/rules inside ``sard/ui/app.py``.
"""

from __future__ import annotations

from typing import Callable, Optional

from tests.helpers.step7_contracts import UIExecutionMode, UIProgressEvent, UIRunResult

SESSION_SERVICE_KEY = "service"
SESSION_REQUEST_KEY = "current_request"
SESSION_EXECUTED_TOKEN_KEY = "executed_run_token"
SESSION_PENDING_TOKEN_KEY = "pending_run_token"
SESSION_PROGRESS_KEY = "progress"
SESSION_RESULT_KEY = "result"
SESSION_DATE_INPUTS_KEY = "date_inputs"
SESSION_RETRY_TOKEN_KEY = "retry_token"
SESSION_DEMO_ENABLED_KEY = "demo_enabled"
SESSION_DEMO_FALLBACK_REQUESTED_KEY = "demo_fallback_requested"

SESSION_KEYS: tuple[str, ...] = (
    SESSION_SERVICE_KEY,
    SESSION_REQUEST_KEY,
    SESSION_EXECUTED_TOKEN_KEY,
    SESSION_PENDING_TOKEN_KEY,
    SESSION_PROGRESS_KEY,
    SESSION_RESULT_KEY,
    SESSION_DATE_INPUTS_KEY,
    SESSION_RETRY_TOKEN_KEY,
    SESSION_DEMO_ENABLED_KEY,
    SESSION_DEMO_FALLBACK_REQUESTED_KEY,
)


class SessionState:
    """A plain-dict stand-in for ``st.session_state`` with Step 7 rules."""

    def __init__(self, store: Optional[dict] = None) -> None:
        self.store = store if store is not None else {}

    def __contains__(self, key: str) -> bool:
        return key in self.store

    def __getitem__(self, key: str):
        return self.store[key]

    def __setitem__(self, key: str, value) -> None:
        self.store[key] = value

    def get(self, key: str, default=None):
        return self.store.get(key, default)

    # -- service instance is per session -------------------------------------
    def service(self, factory: Optional[Callable] = None):
        if SESSION_SERVICE_KEY not in self.store and factory is not None:
            self.store[SESSION_SERVICE_KEY] = factory()
        return self.store.get(SESSION_SERVICE_KEY)

    # -- run-intent / duplicate-run rule --------------------------------------
    def set_run_intent(self, run_token: str) -> None:
        self.store[SESSION_PENDING_TOKEN_KEY] = run_token

    def should_start_run(self) -> bool:
        """Only a new explicit run token may trigger ``stream_run()``."""
        pending = self.store.get(SESSION_PENDING_TOKEN_KEY)
        if not pending:
            return False
        return pending != self.store.get(SESSION_EXECUTED_TOKEN_KEY)

    def mark_run_executed(self, run_token: str) -> None:
        self.store[SESSION_EXECUTED_TOKEN_KEY] = run_token
        self.store.pop(SESSION_PENDING_TOKEN_KEY, None)

    # -- retained progress + result -------------------------------------------
    def append_progress(self, event: UIProgressEvent) -> None:
        self.store.setdefault(SESSION_PROGRESS_KEY, []).append(event)

    def set_result(self, result: UIRunResult) -> None:
        self.store[SESSION_RESULT_KEY] = result

    # -- demo fallback rule -----------------------------------------------------
    def resolve_execution_mode(self, requested: UIExecutionMode) -> UIExecutionMode:
        """Cached-demo is explicit and never falls through to live deps; a live
        run may switch to demo only after the user activates the manual fallback
        control."""
        if requested == UIExecutionMode.CACHED_DEMO:
            return UIExecutionMode.CACHED_DEMO
        if self.store.get(SESSION_DEMO_ENABLED_KEY) and self.store.get(
            SESSION_DEMO_FALLBACK_REQUESTED_KEY
        ):
            return UIExecutionMode.CACHED_DEMO
        return UIExecutionMode.LIVE

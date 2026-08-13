"""Public application boundary for Sard presentation clients.

Exports are loaded lazily so importing the explicitly offline demo module does
not import graph, model, configuration, or retrieval dependencies.
"""

from __future__ import annotations

from importlib import import_module

_CONTRACT_EXPORTS = {
    "CalendarAfterDateRequest",
    "UIArtifactView",
    "UIExecutionMode",
    "UIModeKind",
    "UIModeStatus",
    "UIModelRoute",
    "UIProgressEvent",
    "UIProgressState",
    "UIRunRequest",
    "UIRunResult",
    "UISourceView",
    "UIStage",
}
_SERVICE_EXPORTS = {"ApplicationServiceError", "SardApplicationService"}
_DEMO_EXPORTS = {
    "DemoQueryUnavailable",
    "HERO_QUERY",
    "build_demo_result",
    "is_hero_query",
    "make_demo_run_id",
}

__all__ = sorted(_CONTRACT_EXPORTS | _SERVICE_EXPORTS | _DEMO_EXPORTS)


def __getattr__(name: str):
    if name in _CONTRACT_EXPORTS:
        return getattr(import_module("sard.application.contracts"), name)
    if name in _SERVICE_EXPORTS:
        return getattr(import_module("sard.application.service"), name)
    if name in _DEMO_EXPORTS:
        return getattr(import_module("sard.application.demo"), name)
    raise AttributeError(name)

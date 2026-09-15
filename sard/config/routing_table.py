"""Static capability-aware routing table (workstream B).

Source of truth for TaskClass -> ordered ``(provider, model_id, deadline_s)``
candidates. OpenRouter model IDs are the EXACT canonical free IDs resolved
in ``docs/reports/model_bakeoff_20260915.md`` §2 and
``docs/reports/model_bakeoff_routing_20260915.json`` — human-readable labels
are never used as IDs. Embedding/rerank IDs from the bakeoff brief are NOT
OpenRouter chat IDs, so ``EMBED_*`` legs stay on the NVIDIA path and
``RERANK``/``TTS`` resolve to a deterministic fallback with an
``UNSUPPORTED`` receipt until validated.

Deliberate deviations from the preliminary routing hypothesis, with reasons:

- ``nvidia/nemotron-3.5-lightning:free`` is EXCLUDED from default routes:
  smoke showed ~140s latency (§4) — effectively unusable until retested
  with reasoning disabled. Re-add via env override after a passing retest.
- ``thinkingmachines/inkling[:small]:free`` are EXCLUDED: HTTP 403 via
  plain chat completions ("agentic harnesses only"). Documented in
  :data:`EXCLUDED_IDS`; re-add only behind a harness path.
- ``FAST``/``REWRITE`` second leg: the brief's shorthand
  ``llama-3.1-8b:free`` is not a catalog-verified canonical ID, so the
  static default uses the verified fast small free model
  ``liquid/lfm-2.5-2.6b:free`` (the routing JSON's rewrite primary /
  conversation fallback). Override with ``OPENROUTER_FAST_MODEL_FALLBACK_1``
  / ``OPENROUTER_REWRITE_MODEL_FALLBACK_1`` if desired.
- ``FAST``/``REWRITE`` first leg ``google/gemini-2.0-flash-001`` is the
  operator-configured OpenRouter primary (repo default in
  ``sard/config/models.py``), not a bakeoff free ID — it may be paid.
  Override with ``OPENROUTER_FAST_MODEL_PRIMARY``.

Adapter rules required by the bakeoff (§5) are recorded on
:data:`OPENROUTER_CANDIDATES` notes; enforcing ``reasoning.enabled=false``
and the LFM reasoning-channel reader is provider-adapter work that rides
on empty-content -> next-candidate degradation until implemented.

Every task's OpenRouter legs are env-overridable (``OPENROUTER_*_MODEL_*``);
NVIDIA legs resolve at call time from :func:`sard.config.rag.get_rag_settings`
so existing ``NVIDIA_*_MODEL_*`` variables keep working.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TaskClass(str, Enum):
    """Capability classes routed by :mod:`sard.config.model_router`."""

    FAST_CLASSIFY = "fast_classify"
    QUERY_REWRITE = "query_rewrite"
    PLAN = "plan"
    RESEARCH_SYNTHESIS = "research_synthesis"
    COMPOSE_LONGFORM = "compose_longform"
    COMPOSE_SHORT = "compose_short"
    VERIFY = "verify"
    REPAIR = "repair"
    VISION = "vision"
    STRUCTURED_JSON = "structured_json"
    EMBED_TEXT = "embed_text"
    EMBED_MULTIMODAL = "embed_multimodal"
    RERANK = "rerank"
    TTS = "tts"


@dataclass(frozen=True)
class CandidateFlags:
    """Bakeoff-verified capability flags for one canonical OpenRouter ID."""

    model_id: str
    context_length: int
    supports_tools: bool
    supports_structured: bool
    supports_vision: bool
    note: str = ""


# Exact canonical free IDs from bakeoff report §2 (+ flags from §2 table).
OPENROUTER_CANDIDATES: dict[str, CandidateFlags] = {
    "google/gemma-4-26b-a4b-it:free": CandidateFlags(
        "google/gemma-4-26b-a4b-it:free", 262144, True, True, True,
        note="429 observed in smoke; transient-retry covers free-tier throttle.",
    ),
    "google/gemma-4-31b-it:free": CandidateFlags(
        "google/gemma-4-31b-it:free", 262144, True, True, True,
        note="Routing-JSON structured_output primary; 429 observed, retry covers.",
    ),
    "liquid/lfm-2.5-2.6b:free": CandidateFlags(
        "liquid/lfm-2.5-2.6b:free", 65536, True, True, False,
        note="Reasoning-mandatory endpoint: answers live in the reasoning "
        "channel. Until the adapter reads it, empty content degrades to the "
        "next candidate.",
    ),
    "nvidia/nemotron-3-super-120b-a12b:free": CandidateFlags(
        "nvidia/nemotron-3-super-120b-a12b:free", 262144, True, True, False,
        note="Requires reasoning.enabled=false or reasoning leaks into content.",
    ),
    "nvidia/nemotron-3-ultra-550b-a55b:free": CandidateFlags(
        "nvidia/nemotron-3-ultra-550b-a55b:free", 1000000, True, False, False,
        note="Best T1 Arabic in smoke (7.1s); large-context primary.",
    ),
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": CandidateFlags(
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", 256000, True, False, True,
        note="Slow on free tier (ReadTimeout observed); fallback-only, never "
        "primary for latency-sensitive tasks.",
    ),
    "dots-studio/dots-3-note-preview:free": CandidateFlags(
        "dots-studio/dots-3-note-preview:free", 512000, True, True, True,
        note="Requires reasoning.enabled=false (answer sits in reasoning by default).",
    ),
    "inclusionai/ling-3.0-flash-vl:free": CandidateFlags(
        "inclusionai/ling-3.0-flash-vl:free", 262144, True, False, True,
        note="Requires reasoning.enabled=false; vision primary.",
    ),
}

# Catalog-verified IDs deliberately kept OUT of default routes.
EXCLUDED_IDS: dict[str, str] = {
    "nvidia/nemotron-3.5-lightning:free": "smoke latency ~140s; unusable until reasoning-off retest passes.",
    "thinkingmachines/inkling:free": "HTTP 403 via plain chat completions (agentic harnesses only).",
    "thinkingmachines/inkling-small:free": "HTTP 403 via plain chat completions (agentic harnesses only).",
}

# Operator-configured (possibly paid) OpenRouter primary for fast tasks.
CONFIGURED_FAST_PRIMARY = "google/gemini-2.0-flash-001"

# Per-task default per-candidate budgets (seconds). ChatService callers pass
# a tighter deadline_monotonic; the router always takes the minimum.
TASK_BUDGET_S: dict[TaskClass, float] = {
    TaskClass.FAST_CLASSIFY: 6.0,
    TaskClass.QUERY_REWRITE: 8.0,
    TaskClass.PLAN: 8.0,
    TaskClass.RESEARCH_SYNTHESIS: 20.0,
    TaskClass.COMPOSE_LONGFORM: 20.0,
    TaskClass.COMPOSE_SHORT: 6.0,
    TaskClass.VERIFY: 8.0,
    TaskClass.REPAIR: 8.0,
    TaskClass.VISION: 20.0,
    TaskClass.STRUCTURED_JSON: 8.0,
    TaskClass.EMBED_TEXT: 30.0,
    TaskClass.EMBED_MULTIMODAL: 30.0,
    TaskClass.RERANK: 8.0,
    TaskClass.TTS: 0.0,
}

# Tasks whose OpenRouter legs must be tools/structured-capable first.
STRUCTURED_TASKS = frozenset(
    {TaskClass.PLAN, TaskClass.VERIFY, TaskClass.STRUCTURED_JSON, TaskClass.REPAIR}
)

# Tasks whose OpenRouter legs must be vision-capable.
VISION_TASKS = frozenset({TaskClass.VISION})

# Tasks served deterministically until a path is validated (no model legs).
DETERMINISTIC_TASKS = frozenset({TaskClass.RERANK, TaskClass.TTS})

# Which RAG route backs each task's NVIDIA legs (None = no NVIDIA legs).
NVIDIA_RUNTIME_ROUTE: dict[TaskClass, Optional[str]] = {
    TaskClass.FAST_CLASSIFY: "query",
    TaskClass.QUERY_REWRITE: "query",
    TaskClass.PLAN: "chat",
    TaskClass.RESEARCH_SYNTHESIS: "chat",
    TaskClass.COMPOSE_LONGFORM: "chat",
    TaskClass.COMPOSE_SHORT: "chat",
    TaskClass.VERIFY: "chat",
    TaskClass.REPAIR: "chat",
    TaskClass.VISION: "vision",
    TaskClass.STRUCTURED_JSON: "chat",
    TaskClass.EMBED_TEXT: "embedding",
    TaskClass.EMBED_MULTIMODAL: "embedding",
    TaskClass.RERANK: None,
    TaskClass.TTS: None,
}

# Static OpenRouter legs per task: (model_id, deadline_s). NVIDIA legs are
# appended at call time from RAG settings (see get_route).
DEFAULT_OPENROUTER_ROUTES: dict[TaskClass, tuple[tuple[str, float], ...]] = {
    TaskClass.FAST_CLASSIFY: (
        (CONFIGURED_FAST_PRIMARY, 6.0),
        ("liquid/lfm-2.5-2.6b:free", 6.0),
    ),
    TaskClass.QUERY_REWRITE: (
        (CONFIGURED_FAST_PRIMARY, 8.0),
        ("liquid/lfm-2.5-2.6b:free", 8.0),
    ),
    TaskClass.PLAN: (
        ("nvidia/nemotron-3-super-120b-a12b:free", 8.0),
        ("google/gemma-4-31b-it:free", 8.0),
    ),
    TaskClass.RESEARCH_SYNTHESIS: (
        ("nvidia/nemotron-3-ultra-550b-a55b:free", 20.0),
        ("dots-studio/dots-3-note-preview:free", 20.0),
    ),
    TaskClass.COMPOSE_LONGFORM: (
        ("nvidia/nemotron-3-ultra-550b-a55b:free", 20.0),
        ("dots-studio/dots-3-note-preview:free", 20.0),
    ),
    TaskClass.COMPOSE_SHORT: (
        ("nvidia/nemotron-3-super-120b-a12b:free", 6.0),
        ("google/gemma-4-26b-a4b-it:free", 6.0),
    ),
    TaskClass.VERIFY: (
        ("google/gemma-4-31b-it:free", 8.0),
        ("nvidia/nemotron-3-super-120b-a12b:free", 8.0),
    ),
    TaskClass.REPAIR: (
        ("nvidia/nemotron-3-super-120b-a12b:free", 8.0),
        ("google/gemma-4-31b-it:free", 8.0),
    ),
    TaskClass.VISION: (
        ("inclusionai/ling-3.0-flash-vl:free", 20.0),
        ("dots-studio/dots-3-note-preview:free", 20.0),
        ("google/gemma-4-26b-a4b-it:free", 20.0),
        ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", 20.0),
    ),
    TaskClass.STRUCTURED_JSON: (
        ("google/gemma-4-31b-it:free", 8.0),
        ("nvidia/nemotron-3-super-120b-a12b:free", 8.0),
        ("dots-studio/dots-3-note-preview:free", 8.0),
    ),
    TaskClass.EMBED_TEXT: (),
    TaskClass.EMBED_MULTIMODAL: (),
    TaskClass.RERANK: (),
    TaskClass.TTS: (),
}

# Env-var prefix per task for OPENROUTER_<PREFIX>_MODEL_* overrides.
TASK_ENV_PREFIX: dict[TaskClass, str] = {
    TaskClass.FAST_CLASSIFY: "FAST",
    TaskClass.QUERY_REWRITE: "REWRITE",
    TaskClass.PLAN: "PLAN",
    TaskClass.RESEARCH_SYNTHESIS: "RESEARCH",
    TaskClass.COMPOSE_LONGFORM: "COMPOSE_LONG",
    TaskClass.COMPOSE_SHORT: "COMPOSE_SHORT",
    TaskClass.VERIFY: "VERIFY",
    TaskClass.REPAIR: "REPAIR",
    TaskClass.VISION: "VISION",
    TaskClass.STRUCTURED_JSON: "STRUCTURED",
    TaskClass.EMBED_TEXT: "EMBED",
    TaskClass.EMBED_MULTIMODAL: "EMBED_MM",
    TaskClass.RERANK: "RERANK",
    TaskClass.TTS: "TTS",
}


@dataclass(frozen=True)
class RouteCandidate:
    """One ordered routing leg: provider + model + per-candidate budget."""

    provider: str  # "openrouter" | "nvidia"
    model_id: str  # "" when resolved at call time from the NVIDIA runtime route
    deadline_s: float
    label: str = "primary"
    runtime_route: Optional[str] = None  # "query" | "chat" | "vision" | "embedding"
    degraded: bool = False


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _openrouter_legs(task: TaskClass) -> list[RouteCandidate]:
    """Static OpenRouter legs with env overrides applied (never live-catalog)."""
    prefix = TASK_ENV_PREFIX[task]
    defaults = DEFAULT_OPENROUTER_ROUTES.get(task, ())

    full = _env(f"OPENROUTER_{prefix}_MODELS")
    if full:
        ids = [v.strip() for v in full.split(",") if v.strip()]
        legs = []
        for index, mid in enumerate(ids):
            budget = defaults[index][1] if index < len(defaults) else TASK_BUDGET_S[task]
            legs.append(
                RouteCandidate(
                    provider="openrouter",
                    model_id=mid,
                    deadline_s=budget,
                    label="primary" if index == 0 else f"fallback_{index}",
                    degraded=index > 0,
                )
            )
        return legs

    legs = []
    for index, (mid, budget) in enumerate(defaults):
        env_name = (
            f"OPENROUTER_{prefix}_MODEL_PRIMARY" if index == 0 else f"OPENROUTER_{prefix}_MODEL_FALLBACK_{index}"
        )
        legs.append(
            RouteCandidate(
                provider="openrouter",
                model_id=_env(env_name, mid),
                deadline_s=budget,
                label="primary" if index == 0 else f"fallback_{index}",
                degraded=index > 0,
            )
        )
    return [leg for leg in legs if leg.model_id]


def _nvidia_legs(task: TaskClass, settings: Any) -> list[RouteCandidate]:
    """Expand NVIDIA legs from live RAG settings (existing NVIDIA_* envs)."""
    route_name = NVIDIA_RUNTIME_ROUTE.get(task)
    if route_name is None or settings is None:
        return []
    budget = TASK_BUDGET_S[task]
    try:
        if route_name == "query":
            route = settings.query_route
        elif route_name == "chat":
            route = settings.chat_route
        elif route_name == "vision":
            route = settings.vision_route
        elif route_name == "embedding":
            # Separate-collection rule: the embedding fallback model builds a
            # DIFFERENT vector space (see sard.config.rag + embeddings.py), so
            # it must never silently substitute in the same call. Primary only;
            # total failure degrades deterministically via the router receipt.
            primary = settings.embedding_route.primary
            if not primary or not primary.strip():
                return []
            return [
                RouteCandidate(
                    provider="nvidia",
                    model_id=primary.strip(),
                    deadline_s=budget,
                    label="primary",
                    runtime_route="embedding",
                )
            ]
        else:  # pragma: no cover - defensive
            return []
        ordered = [m for m in route.ordered if m and m.strip()]
    except Exception:
        return []
    return [
        RouteCandidate(
            provider="nvidia",
            model_id=mid.strip(),
            deadline_s=budget,
            label="primary" if index == 0 else f"fallback_{index}",
            runtime_route=route_name,
            degraded=index > 0,
        )
        for index, mid in enumerate(ordered)
    ]


def _apply_capability_filter(task: TaskClass, legs: list[RouteCandidate]) -> list[RouteCandidate]:
    """Drop OpenRouter legs that statically fail the task's capability gate.

    Legs with unknown flags (operator overrides, configured primaries) and
    all NVIDIA legs fail open. Fail-open overall: if the filter would empty
    the route, the unfiltered route is kept so a call is still attempted.
    """
    if task not in STRUCTURED_TASKS and task not in VISION_TASKS:
        return legs
    filtered = []
    for leg in legs:
        if leg.provider != "openrouter":
            filtered.append(leg)
            continue
        flags = OPENROUTER_CANDIDATES.get(leg.model_id)
        if flags is None:
            filtered.append(leg)  # unknown — fail open
            continue
        if task in VISION_TASKS and not flags.supports_vision:
            continue
        if task in STRUCTURED_TASKS and not flags.supports_structured:
            continue
        filtered.append(leg)
    return filtered or legs


def get_route(task: TaskClass, rag_settings: Any = None) -> list[RouteCandidate]:
    """Return the ordered candidate legs for ``task`` (never raises).

    OpenRouter legs come from the static table (+ env overrides); NVIDIA
    legs expand from ``rag_settings`` (loaded via ``get_rag_settings()``
    when omitted; skipped silently when unavailable, e.g. offline tests).
    """
    if task in DETERMINISTIC_TASKS:
        return []
    legs = _apply_capability_filter(task, _openrouter_legs(task))
    settings = rag_settings
    if settings is None:
        try:
            from sard.config.rag import get_rag_settings

            settings = get_rag_settings()
        except Exception:
            settings = None
    legs = list(legs) + _nvidia_legs(task, settings)
    return legs


__all__ = [
    "TaskClass",
    "CandidateFlags",
    "RouteCandidate",
    "OPENROUTER_CANDIDATES",
    "EXCLUDED_IDS",
    "CONFIGURED_FAST_PRIMARY",
    "TASK_BUDGET_S",
    "STRUCTURED_TASKS",
    "VISION_TASKS",
    "DETERMINISTIC_TASKS",
    "NVIDIA_RUNTIME_ROUTE",
    "DEFAULT_OPENROUTER_ROUTES",
    "TASK_ENV_PREFIX",
    "get_route",
]

"""OpenRouter adapter with catalog discovery and free-model filtering.

Uses OpenAI-compatible interface via langchain_openai.ChatOpenAI.
No secrets are logged or exposed to browser.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx

OPENROUTER_API_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

@dataclass(frozen=True)
class OpenRouterModel:
    id: str
    pricing: str  # "free" or "paid"
    context_length: int
    supports_tools: bool
    supports_structured: bool
    supports_vision: bool

def _is_free_pricing(pricing: dict) -> bool:
    # OpenRouter pricing: prompt/completion = "0" for free variants
    try:
        prompt = str(pricing.get("prompt", ""))
        completion = str(pricing.get("completion", ""))
        return prompt == "0" and completion == "0"
    except Exception:
        return False

def fetch_catalog(api_key: Optional[str] = None, timeout: float = 8.0) -> List[OpenRouterModel]:
    """Fetch live catalog from OpenRouter /models endpoint. Bounded timeout, no retry."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = f"{OPENROUTER_API_BASE.rstrip('/')}/models"
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing", {})
        is_free = _is_free_pricing(pricing)
        arch = m.get("architecture", {})
        supports_tools = bool(arch.get("supports_tool_calling") or "tools" in str(m.get("supported_parameters", [])))
        # heuristic for structured output
        supports_structured = "structured_outputs" in str(m.get("supported_parameters", [])) or "response_format" in str(m.get("supported_parameters", []))
        supports_vision = "image" in str(m.get("architecture", {}).get("modality", "")) or "vision" in mid.lower()
        models.append(OpenRouterModel(
            id=mid,
            pricing="free" if is_free else "paid",
            context_length=int(m.get("context_length") or 0),
            supports_tools=supports_tools,
            supports_structured=supports_structured,
            supports_vision=supports_vision,
        ))
    return models

def filter_free_candidates(models: List[OpenRouterModel], require_tools: bool = False, require_vision: bool = False) -> List[OpenRouterModel]:
    out = [m for m in models if m.pricing == "free"]
    if require_tools:
        out = [m for m in out if m.supports_tools]
    if require_vision:
        out = [m for m in out if m.supports_vision]
    return out

def rank_candidates(models: List[OpenRouterModel]) -> List[OpenRouterModel]:
    # Prefer larger context, then lexicographic for determinism
    return sorted(models, key=lambda m: (-m.context_length, m.id))

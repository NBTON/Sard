"""Prompt templates for the agent LangGraph pipeline.

Only safe, generic prompt/JSON-shape strings live here; nothing is ever
routed into safe events.
"""

from __future__ import annotations

from sard.agent.prompts.compose import COMPOSE_SYSTEM_PROMPT, COMPOSE_USER_TEMPLATE
from sard.agent.prompts.plan import (
    PLAN_OUTPUT_KEYS,
    PLAN_SYSTEM_PROMPT,
    PLAN_USER_TEMPLATE,
)
from sard.agent.prompts.understand import (
    UNDERSTAND_OUTPUT_KEYS,
    UNDERSTAND_SYSTEM_PROMPT,
    UNDERSTAND_USER_TEMPLATE,
)
from sard.agent.prompts.verify import (
    VERIFY_OUTPUT_KEYS,
    VERIFY_SYSTEM_PROMPT,
    VERIFY_USER_TEMPLATE,
)

__all__ = [
    "UNDERSTAND_SYSTEM_PROMPT",
    "UNDERSTAND_USER_TEMPLATE",
    "UNDERSTAND_OUTPUT_KEYS",
    "PLAN_SYSTEM_PROMPT",
    "PLAN_USER_TEMPLATE",
    "PLAN_OUTPUT_KEYS",
    "COMPOSE_SYSTEM_PROMPT",
    "COMPOSE_USER_TEMPLATE",
    "VERIFY_SYSTEM_PROMPT",
    "VERIFY_USER_TEMPLATE",
    "VERIFY_OUTPUT_KEYS",
]
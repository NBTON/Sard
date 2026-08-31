"""Shim so integration worker can import via ``sard.agent.capability_registry``.

The canonical registry lives in ``sard.capability_registry`` (single source).
This package re-exports the same symbols so both import paths work:

    from sard.capability_registry import CAPABILITY_REGISTRY
    from sard.agent.capability_registry import CAPABILITY_REGISTRY
"""

from sard.capability_registry import (  # noqa: F401
    CAPABILITY_REGISTRY,
    CapabilityId,
    CapabilitySpec,
    CapabilitySpecModel,
    PIPELINE_PATTERN,
    SupportStatus,
    get_spec,
    limited_ids,
    list_specs,
    registry_as_dict,
    specs_for_intent,
    supported_ids,
    unsupported_ids,
    validate_registry_completeness,
)

__all__ = [
    "CAPABILITY_REGISTRY",
    "CapabilityId",
    "CapabilitySpec",
    "CapabilitySpecModel",
    "PIPELINE_PATTERN",
    "SupportStatus",
    "get_spec",
    "limited_ids",
    "list_specs",
    "registry_as_dict",
    "specs_for_intent",
    "supported_ids",
    "unsupported_ids",
    "validate_registry_completeness",
]

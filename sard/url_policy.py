"""Shared fail-closed policy for external source URLs."""

from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import parse_qsl, unquote, urlsplit


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LENGTH = 2_048
_WHITESPACE_RE = re.compile(r"[\x00-\x20]")
_SENSITIVE_QUERY_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|credential|password|secret|token|"
    r"sig|signature|x-amz-signature|x-amz-credential|x-amz-security-token|"
    r"x-ms-signature|sharedaccesssignature|sas)"
)
_SENSITIVE_MARKER_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:api[_-]?key|authorization|bearer|credential|"
    r"password|secret|token|signature|sharedaccesssignature|sas|nvapi)(?:[^a-z0-9]|$)"
)
_EXPLICIT_CREDENTIAL_PREFIX_RE = re.compile(
    r"(?i)^(?:nvapi|bearer|token|api[_-]?key|authorization|credential|secret)[-_.:=]"
)
_OPAQUE_VALUE_RE = re.compile(r"^[A-Za-z0-9_~+/.=-]+$")
_HEX_RE = re.compile(r"^[A-Fa-f0-9]{32,}$")


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _is_readable_slug(value: str) -> bool:
    words = [word for word in re.split(r"[-_]", value) if word]
    return len(words) >= 3 and all(word.isalpha() and len(word) >= 2 for word in words)


def _looks_like_credential(value: str) -> bool:
    """Detect explicit or opaque credentials without treating length as proof."""

    candidate = unquote(value).strip()
    if _EXPLICIT_CREDENTIAL_PREFIX_RE.search(candidate):
        return True
    if len(candidate) < 32 or _is_readable_slug(candidate):
        return False
    if _HEX_RE.fullmatch(candidate):
        return True
    if not _OPAQUE_VALUE_RE.fullmatch(candidate):
        return False
    character_classes = sum(
        (
            any(character.islower() for character in candidate),
            any(character.isupper() for character in candidate),
            any(character.isdigit() for character in candidate),
        )
    )
    entropy = _entropy(candidate)
    return entropy >= 4.3 or (character_classes >= 2 and entropy >= 4.0)


def safe_external_url(value: object) -> str:
    """Return an unchanged safe source URL, or ``""`` when it may expose a secret."""

    if not isinstance(value, str) or not value or len(value) > _MAX_URL_LENGTH:
        return ""
    if _WHITESPACE_RE.search(value):
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port  # raises ValueError for an invalid port
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError):
        return ""
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not hostname:
        return ""
    if username is not None or password is not None:
        return ""

    for component in (unquote(parsed.path), unquote(parsed.fragment)):
        if _SENSITIVE_MARKER_RE.search(component):
            return ""
        if any(_looks_like_credential(part) for part in component.split("/") if part):
            return ""

    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        decoded_key = unquote(key).strip()
        decoded_value = unquote(item)
        if _SENSITIVE_QUERY_KEY_RE.fullmatch(decoded_key):
            return ""
        if _SENSITIVE_MARKER_RE.search(decoded_key):
            return ""
        if _SENSITIVE_MARKER_RE.search(decoded_value) or _looks_like_credential(decoded_value):
            return ""
    return value


def is_safe_external_url(value: object) -> bool:
    """Return whether ``value`` passes the shared source URL policy."""

    return bool(safe_external_url(value))

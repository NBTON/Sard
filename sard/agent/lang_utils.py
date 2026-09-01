"""Language resolution for Sard — bilingual Arabic/English.

- Treat explicit frontend locale as authoritative.
- Otherwise infer from latest user message.
- Propagate through API, SSE, prompts, artifacts.
"""

from __future__ import annotations

import re
from typing import Optional

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

def detect_language(text: str) -> str:
    """Infer 'ar' if Arabic characters present, else 'en'."""
    if not text:
        return "ar"
    if _ARABIC_RE.search(text):
        return "ar"
    return "en"

def resolve_language(explicit_locale: Optional[str], user_query: str) -> str:
    """Resolve response language: explicit locale wins, else detect from query."""
    if explicit_locale in ("ar", "en"):
        return explicit_locale
    # Handle case where explicit_locale may be "ar-*" etc
    if isinstance(explicit_locale, str) and explicit_locale.lower().startswith("ar"):
        return "ar"
    if isinstance(explicit_locale, str) and explicit_locale.lower().startswith("en"):
        return "en"
    return detect_language(user_query)

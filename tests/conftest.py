"""Shared pytest fixtures/setup for the Sard test suite.

Ensures `sard` is importable even if the project hasn't been `pip install
-e`'d into the active environment, and makes sure no real `.env` file leaks
provider credentials into these tests (tests must run without network
access or API keys).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Deliberately do NOT call `load_dotenv()` here: tests must control
# MODEL_PROVIDER / MODEL_NAME / API keys explicitly via monkeypatch so they
# stay independent of whatever the developer has in their local `.env`.

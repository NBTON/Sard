"""Shared pytest fixtures/setup for the Sard test suite.

Ensures `sard` is importable even if the project hasn't been `pip install
-e`'d into the active environment, and makes sure no real `.env` file leaks
provider credentials into these tests (tests must run without network
access or API keys).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Force the offline contract: server.py honors this to skip `.env` loading,
# so a developer's real provider keys never leak into tests and trigger live
# network calls (non-deterministic, slow, and billed).
os.environ["SARD_DISABLE_DOTENV"] = "1"

# Scrub ambient provider credentials from the developer shell. Without this a
# machine that happens to export OPENROUTER_API_KEY / NVIDIA_API_KEY makes the
# chat endpoint attempt real model calls during tests. Individual tests still
# inject fake keys via monkeypatch when they need to exercise key handling.
for _key in (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "NVIDIA_API_KEY",
    "NVIDIA_CHAT_BASE_URL",
    "NVIDIA_EMBEDDING_BASE_URL",
    "NVIDIA_RERANK_BASE_URL",
    "PARALLEL_API_KEY",
    "TAVILY_API_KEY",
    "TIVALY_API_KEY",
    "EXA_API_KEY",
    "BLOB_READ_WRITE_TOKEN",
    "SARD_BLOB_TOKEN",
    "VERCEL_BLOB_READ_WRITE_TOKEN",
):
    os.environ.pop(_key, None)

# Deliberately do NOT call `load_dotenv()` here: tests must control
# MODEL_PROVIDER / MODEL_NAME / API keys explicitly via monkeypatch so they
# stay independent of whatever the developer has in their local `.env`.
#
# Several runtime modules (server.py, agent/tools/cultural_tools.py,
# agent/tools/multimodal_tools.py, cli/demo.py) call `load_dotenv()` at
# import time. Patching the dotenv entry point to a no-op keeps the scrub
# above effective no matter which module is imported first; tests that want
# a dotenv-style load can call the real function explicitly.
import dotenv as _dotenv  # noqa: E402

_dotenv.load_dotenv = lambda *args, **kwargs: False  # type: ignore[assignment]

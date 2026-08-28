"""Runtime filesystem paths that work locally and on serverless hosts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def output_root(
    configured: str | Path | None = None,
    *,
    default: str | Path = "output",
) -> Path:
    """Resolve an output directory, using Vercel's writable temp area when needed."""

    requested = Path(
        configured or os.environ.get("SARD_OUTPUT_ROOT") or default
    ).expanduser()
    resolved = requested.resolve()
    if not os.environ.get("VERCEL"):
        return resolved

    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temp_root)
    except ValueError:
        # Vercel deploys code under read-only /var/task. Generated artifacts are
        # intentionally ephemeral and must live below its writable /tmp mount.
        return temp_root / "sard-output"
    return resolved

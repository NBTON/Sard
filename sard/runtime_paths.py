"""Runtime filesystem paths that work locally and on serverless hosts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

DEFAULT_VERCEL_BLOB_ENDPOINT = "https://blob.vercel-storage.com"


def durable_storage_configured() -> bool:
    """Return whether a configured remote blob store can outlive a request.

    Vercel's writable ``/tmp`` is instance-local and may be discarded between
    invocations, so it is a development/fallback location, not a download
    persistence guarantee.  The artifact adapter requires both an endpoint and
    a token before considering storage durable.
    """

    endpoint = os.environ.get("SARD_BLOB_ENDPOINT") or (
        DEFAULT_VERCEL_BLOB_ENDPOINT if os.environ.get("BLOB_READ_WRITE_TOKEN") else ""
    )
    token = os.environ.get("SARD_BLOB_TOKEN") or os.environ.get("BLOB_READ_WRITE_TOKEN")
    return bool(endpoint and token)


def output_root_is_ephemeral() -> bool:
    """Whether generated local files may disappear before a later download."""

    return bool(os.environ.get("VERCEL"))


def output_root(
    configured: str | Path | None = None,
    *,
    default: str | Path = "output",
) -> Path:
    """Resolve a local output directory.

    On Vercel this intentionally returns ``/tmp`` only as a safe local
    fallback.  Callers requiring later downloads must configure blob storage;
    this function never treats ``/tmp`` as durable.
    """

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

"""HMAC-signed expiring download URLs for private artifact proxy endpoints.

The Blob store is private; browsers only ever see the server proxy
(``/api/artifacts/{filename}``, ``/api/artifacts/version/{id}/{v}``).
When ``SARD_DOWNLOAD_SECRET`` is configured, download URLs carry
``?exp=<unix>&sig=<hmac>`` and the endpoints reject missing/invalid/
expired signatures — knowing a filename alone is then insufficient.
When the secret is unset (local dev default), URLs stay plain and the
endpoints stay open; ``/api/status`` reports the effective mode so the
open state is never silently mistaken for enforcement.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

_QUERY_EXP = "exp"
_QUERY_SIG = "sig"
_DEFAULT_TTL_S = 3600


def download_secret() -> str:
    """Shared HMAC secret ("" when unconfigured)."""
    try:
        return os.environ.get("SARD_DOWNLOAD_SECRET", "").strip()
    except Exception:
        return ""


def enforcement_mode() -> str:
    """'signed' when the secret is configured, else 'open'."""
    return "signed" if download_secret() else "open"


def _message(resource: str, exp: int) -> bytes:
    return f"{resource}|{int(exp)}".encode("utf-8")


def sign_resource(resource: str, expires_in: int = _DEFAULT_TTL_S) -> str:
    """Return ``?exp=..&sig=..`` for a resource, or "" when open mode."""
    secret = download_secret()
    if not secret:
        return ""
    try:
        ttl = max(60, int(expires_in))
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_S
    exp = int(time.time()) + ttl
    sig = hmac.new(secret.encode("utf-8"), _message(resource, exp), hashlib.sha256).hexdigest()[:48]
    return f"?{_QUERY_EXP}={exp}&{_QUERY_SIG}={sig}"


def verify_resource(resource: str, exp: object, sig: object) -> bool:
    """True when the signature is valid and unexpired (never raises)."""
    secret = download_secret()
    if not secret:
        return False
    try:
        exp_int = int(str(exp or "").strip())
        sig_str = str(sig or "").strip()
    except (TypeError, ValueError):
        return False
    if not sig_str or exp_int <= int(time.time()):
        return False
    expected = hmac.new(secret.encode("utf-8"), _message(resource, exp_int), hashlib.sha256).hexdigest()[:48]
    try:
        return hmac.compare_digest(expected, sig_str)
    except Exception:
        return False


def signed_suffix_for_filename(filename: str, expires_in: int = _DEFAULT_TTL_S) -> str:
    """Query suffix for ``/api/artifacts/{filename}`` URLs."""
    return sign_resource(f"file:{filename}", expires_in)


def verify_filename(filename: str, exp: object, sig: object) -> bool:
    return verify_resource(f"file:{filename}", exp, sig)


def signed_suffix_for_version(artifact_id: str, version: int, expires_in: int = _DEFAULT_TTL_S) -> str:
    """Query suffix for ``/api/artifacts/version/{id}/{v}`` URLs."""
    return sign_resource(f"version:{artifact_id}:{int(version)}", expires_in)


def verify_version(artifact_id: str, version: object, exp: object, sig: object) -> bool:
    try:
        version_int = int(version)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return verify_resource(f"version:{artifact_id}:{version_int}", exp, sig)

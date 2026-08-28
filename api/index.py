"""Vercel Python Function entrypoint for the Sard FastAPI backend."""

from sard.api.server import app

__all__ = ["app"]

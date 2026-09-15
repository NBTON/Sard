"""Vercel Python Function entrypoint for the Sard FastAPI backend."""

from fastapi import FastAPI

from sard.api.server import app as _sard_app

app: FastAPI = _sard_app

__all__ = ["app"]

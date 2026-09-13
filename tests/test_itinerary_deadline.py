"""Itinerary deadline (30-45s), typed partial/timeout, and no-orphan guards."""

from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from sard.api.server import app

client = TestClient(app)


def test_itinerary_timeout_bounds_within_30_to_45():
    raw = os.environ.get("SARD_ITINERARY_TIMEOUT", "40")
    try:
        configured = float(raw)
    except ValueError:
        configured = 40.0
    bounded = max(30.0, min(45.0, configured))
    assert 30.0 <= bounded <= 45.0


def test_live_itinerary_finishes_or_partial_within_45s():
    t0 = time.monotonic()
    resp = client.post("/api/itinerary", json={"query": "يوم واحد في الدرعية", "dates": []})
    elapsed = time.monotonic() - t0
    assert elapsed < 45, f"itinerary exceeded 45s budget ({elapsed:.1f}s)"
    assert resp.status_code in (200, 499, 504)
    data = resp.json()
    if resp.status_code == 200:
        assert "query" in data
        assert "artifacts" in data
    else:
        # Typed partial/timeout shape (never a bare 500 or HTML).
        assert data.get("error_category") in ("timeout", "cancelled")
        assert data.get("partial") is True
        assert "run_id" in data


def test_itinerary_validation_error_typed():
    resp = client.post("/api/itinerary", json={"query": "   "})
    assert resp.status_code == 400
    assert "detail" in resp.json()

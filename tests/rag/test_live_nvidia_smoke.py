"""Opt-in NVIDIA integration smoke test.

The normal suite never loads .env and never contacts a remote endpoint. Set
RAG_LIVE_SMOKE=true in the process environment to run this test deliberately.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from langchain_core.documents import Document

from sard.config.rag import (
    build_chat_model,
    build_embeddings_model,
    build_rerank_model,
    get_rag_settings,
)


# Match the CLI/application boundary while keeping this module explicitly
# opt-in. The offline suite deliberately does not load `.env`.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

if os.environ.get("RAG_LIVE_SMOKE", "").strip().lower() not in {"1", "true", "yes", "on"}:
    pytest.skip("RAG_LIVE_SMOKE is not enabled", allow_module_level=True)

settings = get_rag_settings()
if not settings.nvidia_api_key and not (
    settings.chat_base_url or settings.embedding_base_url or settings.rerank_base_url
):
    pytest.skip("No NVIDIA API key or self-hosted endpoint is configured", allow_module_level=True)


@pytest.mark.live
def test_live_nvidia_embedding_and_generation_smoke():
    embedder = build_embeddings_model(settings.embedding_route.primary, settings)
    vector = embedder.embed_query("اختبار اتصال محلي لنظام سرد")
    assert isinstance(vector, list) and vector and all(isinstance(value, (float, int)) for value in vector)

    model = build_chat_model(settings.chat_route.primary, settings)
    response = model.invoke("أجب بكلمة واحدة: مرحبًا")
    assert getattr(response, "content", "")

    reranker = build_rerank_model(settings.rerank_route.primary, settings)
    ranked = reranker.compress_documents(
        query="الينابيع الحارة",
        documents=[Document(page_content="الينابيع الحارة في الأحساء")],
    )
    assert ranked

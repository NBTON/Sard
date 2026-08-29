"""Additional Step 3 acceptance coverage kept fully offline."""

from __future__ import annotations

import json
import os

import pytest

from sard.config.rag import NVIDIAConfigError, get_rag_settings, list_available_models
from sard.rag.answer import AnswerService
from sard.rag.chunking import chunk_sections
from sard.rag.ingest import IngestionManifest, _discover_source_files
from sard.rag.schemas import Chunk, EmbeddedChunk, ParsedSection
from sard.rag.zvec_store import ZvecRepository, versioned_collection_path


def test_default_nvidia_routes_match_step3_contract(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("NVIDIA_", "RAG_", "ZVEC_")):
            monkeypatch.delenv(key, raising=False)

    settings = get_rag_settings()

    assert settings.chat_route.ordered == (
        "nemotron-3-ultra-550b-a55b",
        "nemotron-3-super-120b-a12b",
        "qwen3-next-80b-a3b-instruct",
    )
    assert settings.query_route.ordered == (
        "nemotron-3-nano-30b-a3b",
        "nvidia-nemotron-nano-9b-v2",
        "llama-3.1-8b-instruct",
    )
    assert settings.embedding_route.primary == "nemotron-3-embed-1b"
    assert settings.embedding_fallback_model == "nv-embed-v1"
    assert settings.rerank_route.primary == "rerank-qa-mistral-4b"
    assert settings.vision_route.primary == "muse-glimmer-30b"


def test_invalid_rag_configuration_fails_before_network(monkeypatch):
    monkeypatch.setenv("RAG_FINAL_TOP_K", "0")
    with pytest.raises(NVIDIAConfigError, match="RAG_FINAL_TOP_K"):
        get_rag_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [("NVIDIA_REQUEST_TIMEOUT_SECONDS", "not-a-number"), ("RAG_ENABLE_FTS", "maybe")],
)
def test_malformed_scalar_configuration_fails_loudly(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(NVIDIAConfigError, match=name):
        get_rag_settings()


def test_unsupported_model_discovery_kind_is_safe():
    assert list_available_models("not-a-route") == []


def test_versioned_path_changes_for_normalization_and_chunking_versions(tmp_path):
    base = str(tmp_path / "zvec")
    current = versioned_collection_path(base, "model", 8, "2", "1", "1")
    normalized_change = versioned_collection_path(base, "model", 8, "2", "2", "1")
    chunking_change = versioned_collection_path(base, "model", 8, "2", "1", "2")
    assert current != normalized_change
    assert current != chunking_change


def test_corrupt_manifest_is_reported_and_recoverable(tmp_path):
    path = tmp_path / "ingestion_manifest.json"
    path.write_text("not-json", encoding="utf-8")
    manifest = IngestionManifest(path)
    assert manifest.load_error
    assert manifest.all_document_ids() == []
    manifest.record("DOC-1", "hash", ["chunk"], "source.md")
    manifest.save()
    assert json.loads(path.read_text(encoding="utf-8"))["DOC-1"]["chunk_ids"] == ["chunk"]


def test_corpus_manifest_is_not_ingested_as_evidence(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "MANIFEST.md").write_text("coverage metadata", encoding="utf-8")
    (corpus / "source.md").write_text("verified source", encoding="utf-8")
    assert _discover_source_files(corpus) == [corpus / "source.md"]


def test_chunking_overlap_never_exceeds_maximum():
    sections = [
        ParsedSection(
            heading="عنوان",
            text=("كلمة " * 800).strip(),
        ),
        ParsedSection(
            heading="عنوان ثان",
            text=("نص " * 800).strip(),
        ),
    ]
    chunks = chunk_sections(sections, target_tokens=650, max_tokens=800)
    assert chunks
    assert all(len(chunk.text.split()) <= 800 for chunk in chunks)


def test_zvec_roundtrip_preserves_sidecar_metadata(tmp_path):
    repo = ZvecRepository.open_or_create(str(tmp_path / "zvec"), "model", 4)
    try:
        chunk = Chunk(
            chunk_id="DOC-1-chunk",
            document_id="DOC-1",
            citation_id="CIT-AAAAAAAAAAAA",
            content="نص موثق",
            content_hash="hash",
            title="عنوان",
            source_name="مصدر",
            source_url="https://example.org/source",
            topic="springs",
            language="ar",
            publication_date=None,
            schema_version="3",
            ingestion_version="1",
            extra_metadata={"verified": True, "archive_id": "A-1"},
        )
        repo.upsert_chunks(
            [EmbeddedChunk(chunk, [1.0, 0.0, 0.0, 0.0], "model", 4)],
            created_at="2024-01-01T00:00:00Z",
        )
        restored = repo.fetch_by_ids([chunk.chunk_id])[chunk.chunk_id]
        assert restored.extra_metadata == {"verified": True, "archive_id": "A-1"}
    finally:
        repo.close()


def test_cli_and_golden_policy_are_explicit():
    from sard.cli.rag import build_parser

    commands = set(build_parser()._subparsers._group_actions[0].choices)
    assert {"create-collection", "resume-ingest", "hybrid-search", "evaluate", "doctor"} <= commands
    golden = json.loads(open("evals/golden.json", encoding="utf-8").read())
    assert golden["evaluation_policy"]["gate_eligible"] is True
    assert golden["evaluation_policy"]["relevance_label_scheme"] == "verified_source_url"
    assert all("gold_source_urls" in case for case in golden["golden_cases"])

"""Automated tests for Sard Multimodal Input capabilities.

Verifies:
1. Image modality: @artifact-photo.jpg identifies object and cultural origin based on extracted features.
2. Audio modality: @oral-history.mp3 transcribes with speaker labels and timestamps.
3. Document modality: @manuscript-scan.pdf extracts and translates text on page 1.
4. 3D modality: @artifact.ply parses vertices and geometry headers.
5. Capability routing: checks correct capability assignment for @file mentions.
6. Grounding: ensures answers cite [Media: ...] and do not hallucinate from filename alone.
"""

import os
import pytest
from pathlib import Path

from sard.agent.capability_routing import Capability, classify_capability
from sard.agent.chat_service import ChatService
from sard.agent.cultural_router import CulturalRouter
from sard.agent.nodes.understand import understand
from sard.agent.state import GraphState, initial_state
from sard.agent.tools.multimodal_tools import (
    FILE_MENTION_PATTERN,
    MultimodalExtractedItem,
    extract_multimodal_context,
    extract_pdf_pages,
    inspect_3d_file,
    inspect_image_core,
    inspect_nifti_file,
    probe_audio_core,
    qwen_audio_transcribe,
    qwen_vl_ocr_extract,
    qwen_vl_vision_analyze,
)


def test_file_mention_regex_patterns():
    """Verify regex accurately identifies various multimodal file extensions."""
    query = (
        "تحليل @artifact-photo.jpg و @oral-history.mp3 و @manuscript-scan.pdf "
        "وكذلك @model.ply و @scan.nii.gz"
    )
    matches = FILE_MENTION_PATTERN.findall(query)
    assert "artifact-photo.jpg" in matches
    assert "oral-history.mp3" in matches
    assert "manuscript-scan.pdf" in matches
    assert "model.ply" in matches
    assert "scan.nii.gz" in matches


def test_capability_routing_multimodal():
    """Verify capability classifier maps @file queries to appropriate multimodal capabilities."""
    assert classify_capability("@artifact-photo.jpg Identify this object and its likely cultural origin.") == Capability.VISION
    assert classify_capability("@oral-history.mp3 Transcribe this with speaker labels and timestamps.") == Capability.AUDIO_TRANSCRIPTION
    assert classify_capability("@manuscript-scan.pdf Extract and translate the text on page 1.") == Capability.DOCUMENT_OCR
    assert classify_capability("@artifact.ply Check mesh dimensions and face count.") == Capability.THREE_D_INSPECTION


def test_image_modality_artifact_photo(tmp_path):
    """Test 1: @artifact-photo.jpg Identify this object and its likely cultural origin."""
    # Create sample image file
    photo_file = tmp_path / "artifact-photo.jpg"
    try:
        from PIL import Image
        img = Image.new("RGB", (640, 480), color="gold")
        img.save(photo_file)
    except Exception:
        photo_file.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xFF\xDB")

    query = f"@{photo_file.name} Identify this object and its likely cultural origin."
    
    mock_files = {
        photo_file.name: {
            "file_type": "image",
            "description": "دلة قهوة عربية حساوية نحاسية مطعمة بالنقوش التراثية من القرن العشرين.",
            "visual_features": {"material": "brass", "pattern": "hasawi_engraving", "type": "coffee_pot"},
            "source_path": str(photo_file),
            "extraction_method": "mock_vision",
        }
    }

    router = CulturalRouter(
        rag_search_fn=lambda q, k: [],
        parallel_search_fn=lambda **kw: [],
        parallel_extract_fn=lambda **kw: [],
        multimodal_extract_fn=lambda q, **kw: extract_multimodal_context(q, base_dir=tmp_path, mock_files=mock_files),
    )

    res = router.answer_query(query, mock_multimodal_files=mock_files)
    assert res.decision.multimodal_extracted_count >= 1
    assert any(c.get("type") == "media" for c in res.citations)
    assert f"[Media: {photo_file.name}]" in res.answer_text or photo_file.name in res.answer_text
    assert "دلة قهوة" in res.answer_text or "بناءً على الفحص البصري" in res.answer_text


def test_audio_modality_oral_history(tmp_path):
    """Test 2: @oral-history.mp3 Transcribe this with speaker labels and timestamps."""
    audio_file = tmp_path / "oral-history.mp3"
    audio_file.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#TSSE\x00\x00\x00\x0f\x00\x00\x03Lavf58.29.100\x00")

    query = f"@{audio_file.name} Transcribe this with speaker labels and timestamps."

    mock_files = {
        audio_file.name: {
            "file_type": "audio",
            "extracted_text": "في الماضي كان آباؤنا في واحة الأحساء يجتمعون عند عين الحارة بعد صلاة العصر.",
            "transcription": {
                "language": "ar",
                "duration_seconds": 25.0,
                "segments": [
                    {
                        "start": "00:00:01",
                        "end": "00:00:12",
                        "speaker": "Speaker 1 (الراوي الحساوي)",
                        "text": "في الماضي كان آباؤنا في واحة الأحساء يجتمعون عند عين الحارة بعد صلاة العصر.",
                    },
                    {
                        "start": "00:00:13",
                        "end": "00:00:24",
                        "speaker": "Speaker 2 (المحاور)",
                        "text": "وكيف كانت تقام مجالس السرد والشعر النبطي؟",
                    },
                ],
            },
            "source_path": str(audio_file),
            "extraction_method": "mock_asr",
        }
    }

    router = CulturalRouter(
        rag_search_fn=lambda q, k: [],
        parallel_search_fn=lambda **kw: [],
        parallel_extract_fn=lambda **kw: [],
        multimodal_extract_fn=lambda q, **kw: extract_multimodal_context(q, base_dir=tmp_path, mock_files=mock_files),
    )

    res = router.answer_query(query, mock_multimodal_files=mock_files)
    assert res.decision.multimodal_extracted_count >= 1
    assert "Speaker 1" in res.answer_text
    assert "00:00:01" in res.answer_text
    assert f"[Media: {audio_file.name}]" in res.answer_text or audio_file.name in res.answer_text


def test_document_modality_manuscript_scan(tmp_path):
    """Test 3: @manuscript-scan.pdf Extract and translate the text on page 1."""
    pdf_file = tmp_path / "manuscript-scan.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")

    query = f"@{pdf_file.name} Extract and translate the text on page 1."

    mock_files = {
        pdf_file.name: {
            "file_type": "document",
            "extracted_text": "وقف تاريخي مؤرخ سنة 1240هـ لتسبيل مياه العيون في واحة الهفوف بالمنطقة الشرقية.",
            "metadata": {"total_pages": 1, "page_number": 1},
            "source_path": str(pdf_file),
            "extraction_method": "mock_ocr",
        }
    }

    router = CulturalRouter(
        rag_search_fn=lambda q, k: [],
        parallel_search_fn=lambda **kw: [],
        parallel_extract_fn=lambda **kw: [],
        multimodal_extract_fn=lambda q, **kw: extract_multimodal_context(q, base_dir=tmp_path, mock_files=mock_files),
    )

    res = router.answer_query(query, mock_multimodal_files=mock_files)
    assert res.decision.multimodal_extracted_count >= 1
    assert "وقف تاريخي" in res.answer_text or "النص المستخرج" in res.answer_text
    assert f"[Media: {pdf_file.name}]" in res.answer_text or pdf_file.name in res.answer_text


def test_3d_modality_inspection(tmp_path):
    """Test 4: @artifact.ply inspect geometry."""
    ply_file = tmp_path / "artifact.ply"
    ply_content = (
        "ply\nformat ascii 1.0\n"
        "element vertex 120\n"
        "property float x\nproperty float y\nproperty float z\n"
        "element face 200\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    )
    ply_file.write_text(ply_content, encoding="utf-8")

    info = inspect_3d_file(ply_file)
    assert info.get("vertices") == 120
    assert info.get("faces") == 200
    assert info.get("format") == "PLY"


def test_understand_node_extracts_multimodal_inputs(tmp_path):
    """Test 5: Verify understand node populates multimodal_inputs in GraphState."""
    state = initial_state(request="@artifact-photo.jpg ما هي دلالة هذه الزخارف؟")
    deps = None
    
    result = understand(state, deps)
    assert "multimodal_inputs" in result
    assert len(result["multimodal_inputs"]) == 1
    assert result["multimodal_inputs"][0].filename == "artifact-photo.jpg"
    assert result["multimodal_inputs"][0].file_type == "image"


def test_grounding_rejects_hallucination_without_evidence():
    """Test 6: Verify Case E prevents guessing when no multimodal or RAG evidence exists."""
    router = CulturalRouter(
        rag_search_fn=lambda q, k: [],
        parallel_search_fn=lambda **kw: [],
        parallel_extract_fn=lambda **kw: [],
        multimodal_extract_fn=lambda q, **kw: [],
    )

    res = router.answer_query("ما هي تقاليد احتفال قرية نائية في سنة 1700؟")
    assert "لم تتوفر مصادر موثقة كافية" in res.answer_text or "Insufficient verified sources" in res.answer_text

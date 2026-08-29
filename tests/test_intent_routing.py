"""Tests for Structured Intent & Capability Routing in Sard."""

from sard.agent.capability_routing import (
    Capability,
    classify_intent,
    classify_capability,
    extract_requested_formats,
    extract_input_modalities,
)


def test_intent_pdf_najd_history():
    intent = classify_intent("أنشئ لي PDF عن تاريخ نجد")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability in (Capability.SAUDI_CULTURAL_FACTUAL, Capability.VERIFIED_RESEARCH)


def test_intent_docx_aseeri_architecture():
    intent = classify_intent("أريد تقرير DOCX عن العمارة العسيرية")
    assert "docx" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability == Capability.SAUDI_CULTURAL_FACTUAL


def test_intent_pptx_founding_day():
    intent = classify_intent("جهز عرض PPTX عن يوم التأسيس")
    assert "pptx" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability == Capability.PRESENTATION_DECK


def test_intent_itinerary_pdf_and_ics():
    intent = classify_intent("برنامج رحلة ثلاثة أيام في الرياض بصيغة PDF وICS")
    assert "pdf" in intent.requested_formats
    assert "ics" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability == Capability.ITINERARY_PLANNING


def test_intent_english_pdf_alula():
    intent = classify_intent("Create a PDF cultural briefing about AlUla")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability in (Capability.SAUDI_CULTURAL_FACTUAL, Capability.VERIFIED_RESEARCH)


def test_intent_english_powerpoint():
    intent = classify_intent("Make a PowerPoint about Najdi architecture")
    assert "pptx" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability == Capability.PRESENTATION_DECK


def test_intent_recipe_pdf_card():
    intent = classify_intent("أعطني وصفة الجريش في بطاقة PDF")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    assert intent.domain_capability == Capability.RECIPE_CARD


def test_input_modalities_from_attachments():
    modalities = extract_input_modalities(
        "يرجى تلخيص هذه الوثيقة",
        attachments=[{"filename": "document.pdf", "mime_type": "application/pdf"}],
    )
    assert "document" in modalities


def test_input_modalities_from_file_mention():
    modalities = extract_input_modalities("ما هذا المعلم في @heritage_site.jpg ؟")
    assert "image" in modalities

"""Tests for Structured Intent & Capability Routing in Sard."""

from sard.agent.capability_routing import (
    Capability,
    classify_intent,
    extract_input_modalities,
)


def test_intent_smalltalk_skips_complex_reasoning():
    """Small-talk (greetings/how-are-you/thanks) is SIMPLE_CONVERSATION even
    though it contains the كيف trigger for explanatory questions."""
    for query in ["كيف حالك؟", "السلام عليكم", "شكرا جزيلا", "hello"]:
        intent = classify_intent(query)
        assert intent.domain_capability == Capability.SIMPLE_CONVERSATION, query
        assert intent.explicit_artifact_request is False


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


def test_dg4_document_request_phrasing_matrix():
    """DG-4: explicit document/report requests infer the pdf default.

    Covers creation/request verbs, explicit formats, informational
    questions, and negated requests.
    """
    from sard.agent.capability_routing import classify_intent

    for query in [
        "I need a document summarizing Asir heritage",
        "I want a report on Diriyah",
        "give me a document about Sadu",
        "prepare a report on Taif roses",
        "provide me with a report on heritage",
        "build a document about museums",
        "design a report on AlUla crafts",
        "produce a document about Diriyah",
        "create a report on Najd",
        "generate a document about AlUla",
    ]:
        intent = classify_intent(query)
        assert "pdf" in intent.requested_formats, query
        assert intent.explicit_artifact_request is True, query

    # Explicit format choice is preserved.
    intent = classify_intent("Create a PDF cultural briefing about AlUla")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True

    # Informational questions never trigger generation.
    for query in ["What is a PDF?", "what is a report?"]:
        intent = classify_intent(query)
        assert intent.requested_formats == ("text",), query
        assert intent.explicit_artifact_request is False, query

    # Negated requests never trigger generation.
    for query in ["do not create a report", "don't generate a document"]:
        intent = classify_intent(query)
        assert intent.requested_formats == ("text",), query
        assert intent.explicit_artifact_request is False, query


def test_dg4_arabic_request_and_negation():
    """DG-4: Arabic request verbs trigger; negated verbs do not."""
    from sard.agent.capability_routing import classify_intent

    intent = classify_intent("\u0623\u0631\u064a\u062f \u062a\u0642\u0631\u064a\u0631\u0627 \u0639\u0646 \u0646\u062c\u062f")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True

    intent = classify_intent("\u0644\u0627 \u062a\u0646\u0634\u0626 \u062a\u0642\u0631\u064a\u0631\u0627")
    assert intent.requested_formats == ("text",)
    assert intent.explicit_artifact_request is False

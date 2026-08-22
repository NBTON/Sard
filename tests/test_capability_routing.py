from sard.agent.capability_routing import classify_capability, Capability, select_route, ModelCandidate

def test_classify():
    assert classify_capability("أين تقع الينابيع الحارة؟") == Capability.SAUDI_CULTURAL_FACTUAL
    assert classify_capability("صمم لي برنامج يومين") == Capability.ITINERARY_PLANNING
    assert classify_capability("ترجم هذا النص") == Capability.TRANSLATION
    assert classify_capability("أرني خريطة الدرعية") == Capability.MAP_GENERATION

def test_select_route_never_silent_mismatch():
    candidates = [
        ModelCandidate("model-a", "openrouter", frozenset([Capability.SIMPLE_CONVERSATION]), 8000, "free", True, False, 1.0),
        ModelCandidate("model-b", "openrouter", frozenset([Capability.VISION]), 8000, "free", False, False, 1.0),
    ]
    # vision requires explicit vision capability
    res = select_route(Capability.VISION, candidates)
    assert res is None or "vision" in res.capabilities

def test_auto_prefers_free():
    c_free = ModelCandidate("free-model", "openrouter", frozenset([Capability.SIMPLE_CONVERSATION]), 16000, "free", True, True, 0.9)
    c_paid = ModelCandidate("paid-model", "openrouter", frozenset([Capability.SIMPLE_CONVERSATION]), 32000, "paid", True, True, 1.0)
    res = select_route(Capability.SIMPLE_CONVERSATION, [c_free, c_paid], free_only=True)
    assert res.model_id == "free-model"

def test_fallback_when_no_free():
    c_paid = ModelCandidate("paid-model", "openrouter", frozenset([Capability.SIMPLE_CONVERSATION]), 32000, "paid", True, True, 1.0)
    res = select_route(Capability.SIMPLE_CONVERSATION, [c_paid], free_only=True)
    # if no free pool, fallback to paid is allowed via separate path; here our logic returns paid only if free_pool empty -> we filter free only if free exists
    # So with only paid, it should still return
    assert res is not None

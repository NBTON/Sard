"""G9: registry fallback docs must match capability_unavailable truth."""
from sard.capability_registry import CAPABILITY_REGISTRY

def test_gap_g9_registry_audio_vision_truthful():
    specs = list(CAPABILITY_REGISTRY.values())
    texts = " ".join(getattr(s,'fallback','') for s in specs if hasattr(s,'fallback'))
    assert "Hasawi oasis oral history template with 2 mock" not in texts
    assert "templated cultural artifact description" not in texts
    assert "capability_unavailable" in texts
    # ICS must not promise first-4
    assert "HERITAGE_EVENTS_DATABASE[:4] when search yields empty" not in texts

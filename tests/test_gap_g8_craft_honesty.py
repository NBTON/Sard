"""G8: empty/unknown craft must be explicit, no silent sadu."""
from sard.agent.tools.cultural_agentic_tools import tool_advise_artisan_craft

def test_gap_g8_craft_empty_is_missing_input():
    res = tool_advise_artisan_craft(craft_name="")
    assert res["success"] is False
    assert res.get("error_category") == "missing_input"

def test_gap_g8_craft_unknown_is_explicit():
    res = tool_advise_artisan_craft(craft_name="xyzunknown123")
    assert res["success"] is False
    assert "غير" in res.get("message_ar","")

def test_gap_g8_craft_known_still_works():
    res = tool_advise_artisan_craft(craft_name="السدو")
    assert res["success"] is True

"""G5: unknown recipe without inputs must not emit empty sections."""
from sard.agent.tools.cultural_agentic_tools import tool_generate_recipe_or_craft_card

def test_gap_g5_recipe_unknown_returns_no_match():
    res = tool_generate_recipe_or_craft_card(item_name="طبق فضائي مجهول xyz123")
    assert res["success"] is False
    assert res.get("error_category") == "no_match"
    assert res.get("filename") is None
    # Known jareesh still works
    res2 = tool_generate_recipe_or_craft_card(item_name="الجريش")
    assert res2["success"] is True
    assert res2["filename"].endswith(".pdf")

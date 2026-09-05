"""G7: empty calendar filters must not emit first-4 canned events."""
from sard.agent.tools.cultural_agentic_tools import tool_sync_heritage_calendar

def test_gap_g7_calendar_empty_requires_filters():
    res = tool_sync_heritage_calendar(query="", category=None, region=None, month=None)
    assert res["success"] is False
    assert res.get("error_category") == "missing_filters"
    assert res["total_events"] == 0
    assert res["filename"] is None

def test_gap_g7_calendar_filtered_no_match_is_empty():
    res = tool_sync_heritage_calendar(query="حدث مستحيل xyz999", category="nope", region="nowhere")
    assert res["total_events"] == 0
    assert res["filename"] is None

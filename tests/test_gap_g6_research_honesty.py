"""G6: research template must be labeled template, not verified."""
from sard.agent.tools.cultural_agentic_tools import tool_conduct_verified_research

def test_gap_g6_research_template_labeled():
    res = tool_conduct_verified_research(topic="توثيق قصر المربع")
    assert res["success"] is True
    assert res.get("verification") == "template"
    assert "template_timeline_not_verified" in (res.get("warnings") or [])
    assert "غير موثق" in res.get("message_ar","") or "قالب" in res.get("message_ar","")

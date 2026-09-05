"""G2: ONE capability service + ONE artifact contract (no duplicate orchestrate_from_intent)."""
import inspect
from sard.outputs import orchestrator as orch_mod
from sard.outputs.orchestrator import ArtifactOrchestrator

def test_gap_g2_single_orchestrate_from_intent():
    # Exactly one definition must exist (no dead shim shadowing)
    src = inspect.getsource(orch_mod)
    assert src.count("def orchestrate_from_intent") == 1
    sig = inspect.signature(ArtifactOrchestrator.orchestrate_from_intent)
    params = list(sig.parameters.keys())
    # Intent version: (self, intent, raw_text, content_data, sources)
    assert "intent" in params
    assert "raw_text" in params
    # generate_artifact supports deadline guard (G11)
    sig2 = inspect.signature(ArtifactOrchestrator.generate_artifact)
    assert "deadline_monotonic" in sig2.parameters

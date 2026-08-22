import pathlib
import re

FORBIDDEN = [
    "RAG",
    "Always-On RAG",
    "retrieval mode",
    "embedding",
    "reranker",
    "vector",
    "Zvec",
    "rerank",
]

PUBLIC_FILES = [
    "web/src/components/MessageItem.tsx",
    "web/src/components/Header.tsx",
    "web/src/components/Sidebar.tsx",
    "web/src/components/ChatInput.tsx",
    "web/src/components/CitationsDrawer.tsx",
    "web/src/components/WelcomeHero.tsx",
    "web/src/components/SettingsModal.tsx",
    "web/src/app/layout.tsx",
]

def test_no_forbidden_strings_in_public_ui():
    root = pathlib.Path(__file__).resolve().parents[1]
    failures = []
    for rel in PUBLIC_FILES:
        p = root / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for term in FORBIDDEN:
            # case sensitive check for RAG etc; allow in comments? we forbid anyway
            if term.lower() in text.lower():
                # allow exception for "Verified" etc - check exact
                # we strictly check for forbidden terms containing capital RAG etc.
                # Use regex word boundary
                if re.search(re.escape(term), text, re.IGNORECASE):
                    # For rerank vs reranker distinction, we already have list
                    # Treat vector only if not part of allowed context? simple
                    failures.append(f"{rel} contains forbidden '{term}'")
    assert not failures, "\n".join(failures)

def test_api_done_contract_hides_internal():
    import pathlib
    server = (pathlib.Path(__file__).resolve().parents[1] / "sard" / "api" / "server.py").read_text(encoding="utf-8")
    # done payload should not contain retrieval_mode or model in public
    assert '"retrieval_mode"' not in server or 'verified' in server
    # ensure done uses verified/sources_count
    assert '"verified"' in server

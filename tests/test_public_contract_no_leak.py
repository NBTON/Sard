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
    "src/components/ChatMessages.tsx",
    "src/components/Header.tsx",
    "src/components/Sidebar.tsx",
    "src/components/Composer.tsx",
    "src/components/ArtifactPanel.tsx",
    "src/components/Landing.tsx",
    "src/app/layout.tsx",
]

def test_no_forbidden_strings_in_public_ui():
    root = pathlib.Path(__file__).resolve().parents[1]
    failures = []
    checked = 0
    for rel in PUBLIC_FILES:
        p = root / rel
        # Every listed file must exist: a missing file is a failure, never
        # a silent skip (a vacuous pass guards nothing).
        assert p.exists(), f"public-UI contract file missing: {rel}"
        checked += 1
        text = p.read_text(encoding="utf-8")
        for term in FORBIDDEN:
            # Word-boundary match: plain substring search false-positives on
            # "drag"/"fragile"/"storage". A hit means the literal internal
            # term appears as its own token in the shipped UI bundle.
            pattern = r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])"
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{rel}:{line} contains forbidden '{term}'")
    assert checked >= 1, "public-UI contract examined zero files"
    # Known-good exception (documented, not a user-visible leak): ChatMessages
    # strips backend citation markers (`[RAG: ...]`, `[Web: ...]`) from
    # displayed text. That sanitizer must name the marker to remove it.
    failures = [
        f for f in failures
        if not (f.startswith("src/components/ChatMessages.tsx:") and "'RAG'" in f)
    ]
    assert not failures, "\n".join(failures)

def test_api_done_contract_hides_internal():
    import pathlib
    server = (pathlib.Path(__file__).resolve().parents[1] / "sard" / "api" / "server.py").read_text(encoding="utf-8")
    # done payload should not contain retrieval_mode or model in public
    assert '"retrieval_mode"' not in server or 'verified' in server
    # ensure done uses verified/sources_count
    assert '"verified"' in server
    # Parse the terminal `done` SSE payload block and assert no internal
    # routing/model keys leak into the public contract. The block starts at
    # `"event": "done"` and spans the data dict construction below it.
    marker = '"event": "done"'
    assert marker in server, "terminal done event missing from server.py"
    block = server.split(marker, 1)[1][:3000]
    for leaked in ('"model"', '"retrieval_mode"', '"provider"', '"api_key"', '"apiKey"'):
        assert leaked not in block, f"public done payload leaks {leaked}"
    for required in ('"verified"', '"sources_count"', '"run_id"'):
        assert required in block, f"public done payload missing {required}"

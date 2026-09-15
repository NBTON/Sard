"""Bakeoff runner for OpenRouter models evaluation (2026-09-15).

Reads API key ONLY from process environment or primary ignored .env.local/.env.
NEVER prints, logs, echoes, writes, or persists any secret.
Outputs raw results and scoring summaries containing only model IDs, prompts,
latencies, responses, and metrics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_REPO_ROOT = Path(r"C:\Users\nawaf\OneDrive - KFUPM\Sard_Agent")

SUITE_PATH = ROOT / "evals" / "bakeoff_suite_20260915.json"
ROUTING_PATH = ROOT / "docs" / "reports" / "model_bakeoff_routing_20260915.json"


def _secure_load_env() -> None:
    """Load OPENROUTER_API_KEY into os.environ if missing, without logging values."""
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return
    candidate_files = [
        PRIMARY_REPO_ROOT / ".env.local",
        PRIMARY_REPO_ROOT / ".env",
        ROOT / ".env.local",
        ROOT / ".env",
    ]
    for p in candidate_files:
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k == "OPENROUTER_API_KEY" and k not in os.environ:
                        os.environ[k] = v.strip().strip('"').strip("'")
                        return
            except Exception:
                pass


_secure_load_env()


def get_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def load_suite() -> Dict[str, Any]:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def candidate_ids() -> List[str]:
    try:
        routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))
        ids = sorted({x for r in routing.get("routes", []) for x in (r.get("primary"), r.get("fallback")) if x})
        return [i for i in ids if ":free" in i]
    except Exception:
        return []


def is_free_pricing(pricing: Dict[str, Any]) -> bool:
    try:
        p = str(pricing.get("prompt", ""))
        c = str(pricing.get("completion", ""))
        return p == "0" and c == "0"
    except Exception:
        return False


def fetch_live_catalog(timeout_s: float = 15.0) -> Dict[str, Any]:
    """Fetch catalog live from OpenRouter and extract metadata."""
    import httpx

    key = get_api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base}/models"
    t0 = time.time()
    resp = httpx.get(url, headers=headers, timeout=timeout_s)
    dt = round(time.time() - t0, 2)
    resp.raise_for_status()
    data = resp.json()
    raw_models = data.get("data", [])
    fetched_at = datetime.now(timezone.utc).isoformat()

    catalog_models = []
    free_count = 0
    for m in raw_models:
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing", {})
        free = is_free_pricing(pricing)
        if free:
            free_count += 1
        arch = m.get("architecture", {})
        params = m.get("supported_parameters", [])
        tools = bool(arch.get("supports_tool_calling") or "tools" in str(params))
        struct = "structured_outputs" in str(params) or "response_format" in str(params)
        vision = "image" in str(arch.get("modality", "")) or "vision" in mid.lower()
        catalog_models.append({
            "id": mid,
            "name": m.get("name", ""),
            "pricing": "free" if free else "paid",
            "context_length": int(m.get("context_length") or 0),
            "supports_tools": tools,
            "supports_structured": struct,
            "supports_vision": vision,
        })

    return {
        "fetched_at_utc": fetched_at,
        "latency_s": dt,
        "total_count": len(catalog_models),
        "free_count": free_count,
        "models": catalog_models,
    }


def run_chat(
    model_id: str,
    prompt: str,
    max_tokens: int = 220,
    temperature: float = 0.2,
    timeout_s: float = 30.0,
    disable_reasoning: bool = False,
    response_format: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a single chat completion request with robust extraction and timing."""
    import httpx

    key = get_api_key()
    if not key:
        return {"ok": False, "error": "missing-key", "text": "", "latency_s": 0.0, "extra": {}}

    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body: Dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if disable_reasoning:
        body["reasoning"] = {"enabled": False}
    if response_format:
        if isinstance(response_format, str):
            body["response_format"] = {"type": response_format}
        else:
            body["response_format"] = response_format

    t0 = time.time()
    resp = None
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout_s)
        dt = time.time() - t0
        # If rate limited (429), polite single retry after 2.5s
        if resp.status_code == 429:
            time.sleep(2.5)
            t0 = time.time()
            resp = httpx.post(url, headers=headers, json=body, timeout=timeout_s)
            dt = time.time() - t0

        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"http-{resp.status_code}",
                "text": "",
                "latency_s": round(dt, 2),
                "extra": {"status_code": resp.status_code, "body_prefix": resp.text[:250]},
            }

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"ok": False, "error": "no-choices", "text": "", "latency_s": round(dt, 2), "extra": {}}

        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join([b.get("text", "") for b in content if isinstance(b, dict)])

        extra: Dict[str, Any] = {}
        for r_key in ("reasoning", "reasoning_content", "reasoning_details"):
            if msg.get(r_key):
                extra[r_key] = str(msg.get(r_key))[:300]

        # If content is empty but reasoning has text, extract from reasoning
        if not content.strip():
            for r_key in ("reasoning", "reasoning_content"):
                if msg.get(r_key):
                    content = str(msg.get(r_key))
                    extra["extracted_from"] = r_key
                    break

        if not content.strip() and choices[0].get("text"):
            content = choices[0]["text"]
            extra["extracted_from"] = "choices[0].text"

        text = content.strip()
        return {
            "ok": bool(text),
            "error": "" if text else "empty",
            "text": text[:4000],
            "latency_s": round(dt, 2),
            "extra": extra,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "text": "",
            "latency_s": round(time.time() - t0, 2),
            "extra": {"exception": str(e)[:200]},
        }


def score_response(task_id: str, text: str, ok: bool, latency_s: float) -> Dict[str, Any]:
    """Score a response along objective axes based on task requirements."""
    scores: Dict[str, Any] = {"ok": ok, "latency_s": latency_s}
    if not ok or not text.strip():
        scores["arabic_fluency_fidelity"] = 0
        scores["failure"] = 1
        return scores

    scores["failure"] = 0

    # Arabic script presence
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    total_chars = len(text)
    is_primarily_arabic = (arabic_chars / max(total_chars, 1)) > 0.3

    # Fluency heuristic
    if is_primarily_arabic:
        scores["arabic_fluency_fidelity"] = 2
    elif arabic_chars > 10:
        scores["arabic_fluency_fidelity"] = 1
    else:
        scores["arabic_fluency_fidelity"] = 0

    # Markdown structure (T3, T4, T6, T11)
    has_headers = bool(re.search(r"^#+\s+.+", text, re.MULTILINE))
    has_lists = bool(re.search(r"^\s*[-*•\d+\.]\s+.+", text, re.MULTILINE))
    if has_headers or has_lists:
        scores["markdown_structure"] = 2 if (has_headers and has_lists) or has_lists else 1
    else:
        scores["markdown_structure"] = 0

    # Table formatting (T6)
    if task_id == "T6-table":
        table_lines = [l for l in text.splitlines() if "|" in l]
        if len(table_lines) >= 3 and any("-" in l for l in table_lines):
            scores["tables"] = 2
        elif len(table_lines) >= 2:
            scores["tables"] = 1
        else:
            scores["tables"] = 0

    # JSON reliability (T8)
    if task_id == "T8-structured-json":
        # extract JSON block if wrapped in markdown
        json_str = text
        if "```json" in text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                json_str = m.group(1)
        elif "{" in text and "}" in text:
            m = re.search(r"(\{.*\})", text, re.DOTALL)
            if m:
                json_str = m.group(1)
        try:
            parsed = json.loads(json_str)
            has_keys = all(k in parsed for k in ("topic", "region", "days", "stops"))
            valid_stops = isinstance(parsed.get("stops"), list) and len(parsed.get("stops", [])) >= 2
            scores["json_reliability"] = 2 if (has_keys and valid_stops) else 1
            scores["json_parsed"] = True
        except Exception:
            scores["json_reliability"] = 0
            scores["json_parsed"] = False

    # Citation obedience / abstention (T1, T2, T7, T10, T11)
    # Check for hallucinated CIT- tags or fake URLs
    invented_cit = bool(re.search(r"\[CIT-\d+\]", text))
    fake_urls = bool(re.search(r"https?://", text))
    if invented_cit or fake_urls:
        scores["citation_obedience"] = 0
    else:
        scores["citation_obedience"] = 2

    # Verification specific (T10)
    if task_id == "T10-fact-verification":
        # Correct answer is that Al-Ahsa was inscribed in UNESCO in 2018 (صحيحة)
        if "صحيحة" in text:
            scores["verification_correct"] = True
        elif "توثيق" in text:
            scores["verification_correct"] = "hedged"
        else:
            scores["verification_correct"] = False

    return scores


def main() -> None:
    ap = argparse.ArgumentParser(description="Sard OpenRouter Model Bakeoff Runner")
    ap.add_argument("--catalog", action="store_true", help="Fetch and print live catalog metadata")
    ap.add_argument("--list", action="store_true", help="List tasks and routing candidate models")
    ap.add_argument("--smoke", action="store_true", help="Run quick smoke test across candidates on T1")
    ap.add_argument("--benchmark", action="store_true", help="Run bounded useful benchmark across all 11 tasks")
    ap.add_argument("--models", nargs="*", help="Specific model IDs to test")
    ap.add_argument("--out", default=r"C:\Users\nawaf\.gemini\antigravity-cli\brain\0a09c51f-3f8b-4cdd-b6db-6060659c3d24\scratch\bakeoff_results_20260915.json", help="Path to save raw results")
    args = ap.parse_args()

    key_present = bool(get_api_key())
    print(f"Key present: {key_present} (loaded securely, never printed/logged)", flush=True)

    if args.catalog:
        print("Fetching live catalog from OpenRouter /models...", flush=True)
        cat = fetch_live_catalog()
        print(f"Timestamp UTC: {cat['fetched_at_utc']}", flush=True)
        print(f"Total models: {cat['total_count']}, Free models: {cat['free_count']}, Latency: {cat['latency_s']}s", flush=True)
        print("Candidate models in catalog:", flush=True)
        cands = [
            "nvidia/nemotron-3.5-lightning:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "google/gemma-4-26b-a4b-it:free",
            "google/gemma-4-31b-it:free",
            "liquid/lfm-2.5-2.6b:free",
            "dots-studio/dots-3-note-preview:free",
            "inclusionai/ling-3.0-flash-vl:free",
            "thinkingmachines/inkling-small:free",
        ]
        found_map = {m["id"]: m for m in cat["models"]}
        for c in cands:
            if c in found_map:
                m = found_map[c]
                print(f"  [FOUND] {c} (ctx={m['context_length']}, tools={m['supports_tools']}, struct={m['supports_structured']}, vision={m['supports_vision']})", flush=True)
            else:
                print(f"  [MISSING] {c}", flush=True)
        return

    suite = load_suite()
    default_cands = candidate_ids()
    target_models = args.models if args.models else default_cands

    if args.list:
        print(f"Suite tasks ({len(suite['tasks'])} total):", flush=True)
        for t in suite["tasks"]:
            print(f"  {t['id']}: max_tokens={t['gen'].get('max_tokens')} axes={t.get('scored_axes')}", flush=True)
        print(f"\nRouting candidate models ({len(target_models)} total):", flush=True)
        for m in target_models:
            print(f"  {m}", flush=True)
        return

    if not key_present:
        print("ERROR: OPENROUTER_API_KEY missing from environment and .env.local", flush=True)
        sys.exit(2)

    if args.smoke:
        print("Running smoke check on T1-arabic-factual...", flush=True)
        t1 = next(x for x in suite["tasks"] if x["id"] == "T1-arabic-factual")
        for mid in target_models:
            dis_reason = ("super" in mid or "dots" in mid or "ling" in mid)
            res = run_chat(mid, t1["prompt_ar"], max_tokens=t1["gen"]["max_tokens"], disable_reasoning=dis_reason)
            status = "OK" if res["ok"] else f"FAIL({res['error']})"
            preview = res["text"][:80].replace("\n", " ")
            print(f"  {mid:50} :: {status:12} :: {res['latency_s']:5.2f}s :: {preview}", flush=True)
            time.sleep(1.5)
        return

    if args.benchmark:
        print(f"Running bounded benchmark across ALL {len(suite['tasks'])} tasks for {len(target_models)} models...", flush=True)
        out_results: List[Dict[str, Any]] = []
        model_stats: Dict[str, Dict[str, Any]] = {
            m: {"requests": 0, "success": 0, "failure": 0, "latencies": [], "scores": []}
            for m in target_models
        }

        for t in suite["tasks"]:
            tid = t["id"]
            prompt = t["prompt_ar"]
            max_tok = t["gen"].get("max_tokens", 220)
            resp_fmt = t["gen"].get("response_format")
            print(f"\n--- Task {tid} (max_tokens={max_tok}) ---", flush=True)
            for mid in target_models:
                dis_reason = ("super" in mid or "dots" in mid or "ling" in mid)
                res = run_chat(
                    mid,
                    prompt,
                    max_tokens=max_tok,
                    disable_reasoning=dis_reason,
                    response_format=resp_fmt,
                )
                sc = score_response(tid, res["text"], res["ok"], res["latency_s"])
                out_results.append({
                    "task_id": tid,
                    "model_id": mid,
                    "result": res,
                    "scores": sc,
                })
                m_stat = model_stats[mid]
                m_stat["requests"] += 1
                if res["ok"]:
                    m_stat["success"] += 1
                    m_stat["latencies"].append(res["latency_s"])
                else:
                    m_stat["failure"] += 1
                m_stat["scores"].append(sc)

                status = "OK" if res["ok"] else f"FAIL({res['error']})"
                preview = res["text"][:70].replace("\n", " ")
                print(f"  {mid:45} | {status:10} | {res['latency_s']:5.2f}s | {preview}", flush=True)
                time.sleep(1.5)

        # Summarize
        print("\n=== BENCHMARK SUMMARY ===", flush=True)
        summary = {}
        for mid, st in model_stats.items():
            reqs = st["requests"]
            succ = st["success"]
            fail = st["failure"]
            lats = st["latencies"]
            med_lat = sorted(lats)[len(lats) // 2] if lats else 0.0
            avg_lat = round(sum(lats) / len(lats), 2) if lats else 0.0

            # Compute category averages
            ar_scores = [s.get("arabic_fluency_fidelity", 0) for s in st["scores"] if "arabic_fluency_fidelity" in s]
            cit_scores = [s.get("citation_obedience", 0) for s in st["scores"] if "citation_obedience" in s]
            md_scores = [s.get("markdown_structure", 0) for s in st["scores"] if "markdown_structure" in s]
            tbl_scores = [s.get("tables", 0) for s in st["scores"] if "tables" in s]
            json_scores = [s.get("json_reliability", 0) for s in st["scores"] if "json_reliability" in s]

            summary[mid] = {
                "requests": reqs,
                "success": succ,
                "failure": fail,
                "success_rate": round(succ / max(reqs, 1), 2),
                "median_latency_s": med_lat,
                "average_latency_s": avg_lat,
                "arabic_fluency_avg": round(sum(ar_scores) / max(len(ar_scores), 1), 2),
                "citation_obedience_avg": round(sum(cit_scores) / max(len(cit_scores), 1), 2),
                "markdown_structure_avg": round(sum(md_scores) / max(len(md_scores), 1), 2),
                "tables_score": tbl_scores[0] if tbl_scores else None,
                "json_score": json_scores[0] if json_scores else None,
            }
            print(f"{mid:45}: {succ}/{reqs} passed, p50={med_lat}s, ar_fluency={summary[mid]['arabic_fluency_avg']}/2, fail={fail}", flush=True)

        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tasks_count": len(suite["tasks"]),
            "models_tested": target_models,
            "summary": summary,
            "results": out_results,
        }
        if args.out:
            out_file = Path(args.out)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nRaw results securely saved to: {out_file} (no secrets stored)", flush=True)
        return

    ap.print_help()


if __name__ == "__main__":
    main()

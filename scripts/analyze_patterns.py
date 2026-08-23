"""
Computes cross-app patterns from data/results.json and writes data/patterns.json.

Everything here is a real aggregation over whatever's currently in results.json --
it runs at any coverage level and reports honestly on however many apps are
done so far. Nothing here is invented; category-level insight lines are only
generated when the underlying counts actually support them (a minimum sample
size per category, not just "one gated app therefore this category is gated").

Usage: python analyze_patterns.py
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"
PATTERNS_FILE = DATA_DIR / "patterns.json"

MIN_CATEGORY_SAMPLE = 5  # don't draw a category-level conclusion from fewer apps than this


def pct(n, total):
    return round(100 * n / total, 1) if total else 0.0


def main():
    results = json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []
    total = len(results)

    if total == 0:
        PATTERNS_FILE.write_text(json.dumps({"note": "no results yet"}, indent=2))
        print("No results yet -- run research_agent.py first.")
        return

    # Auth distribution
    auth_counter = Counter()
    for r in results:
        for a in r.get("auth_methods", []):
            auth_counter[a] += 1

    # Credential access
    access_counter = Counter(r.get("credential_access", "unclear") for r in results)

    # API protocols
    protocol_counter = Counter()
    for r in results:
        protocols = tuple(sorted(r.get("api_protocols", []))) or ("none",)
        protocol_counter[protocols] += 1

    # MCP / Composio catalog
    composio_toolkit_counter = Counter(
        (r.get("composio_catalog") or {}).get("composio_toolkit_exists", "unknown") for r in results
    )

    # Buildability
    verdict_counter = Counter(r.get("buildability_verdict", "unclear") for r in results)

    # Blockers (grouped)
    blocker_counter = Counter()
    for r in results:
        b = (r.get("blocker") or "").strip()
        if b:
            blocker_counter[b] += 1

    # Confidence
    confidence_counter = Counter(r.get("confidence", "unclear") for r in results)

    # Category-level breakdown (only with enough samples to say something real)
    by_category = defaultdict(list)
    for r in results:
        by_category[r.get("category", "unknown")].append(r)

    category_analysis = {}
    key_findings = []

    for cat, apps in by_category.items():
        n = len(apps)
        gated = sum(1 for a in apps if a.get("credential_access") == "gated")
        ready = sum(1 for a in apps if a.get("buildability_verdict") == "ready")
        category_analysis[cat] = {
            "n": n,
            "gated_pct": pct(gated, n),
            "ready_pct": pct(ready, n),
        }

    # Only generate a comparative finding when at least two categories clear the sample floor
    eligible = {c: v for c, v in category_analysis.items() if v["n"] >= MIN_CATEGORY_SAMPLE}
    if len(eligible) >= 2:
        most_gated = max(eligible.items(), key=lambda kv: kv[1]["gated_pct"])
        least_gated = min(eligible.items(), key=lambda kv: kv[1]["gated_pct"])
        if most_gated[1]["gated_pct"] > least_gated[1]["gated_pct"]:
            key_findings.append(
                f"{most_gated[0]} has the highest gated-credential rate among categories with "
                f"{MIN_CATEGORY_SAMPLE}+ researched apps ({most_gated[1]['gated_pct']}%), "
                f"vs. {least_gated[0]} at {least_gated[1]['gated_pct']}%."
            )
    else:
        key_findings.append(
            f"Not enough apps researched per category yet (need {MIN_CATEGORY_SAMPLE}+ in at "
            f"least 2 categories) to draw a reliable category comparison."
        )

    if auth_counter:
        top_auth, top_auth_n = auth_counter.most_common(1)[0]
        key_findings.append(f"{top_auth} is the most common auth method so far ({top_auth_n}/{total} apps).")

    if access_counter:
        key_findings.append(
            f"{pct(access_counter.get('self-serve',0), total)}% self-serve vs. "
            f"{pct(access_counter.get('gated',0), total)}% gated so far."
        )

    patterns = {
        "coverage": {"researched": total, "of_total": 100, "pct": pct(total, 100)},
        "auth_distribution": dict(auth_counter),
        "credential_access": {k: {"count": v, "pct": pct(v, total)} for k, v in access_counter.items()},
        "api_protocols": {"+".join(k): v for k, v in protocol_counter.items()},
        "composio_toolkit_exists": {str(k): v for k, v in composio_toolkit_counter.items()},
        "buildability": {k: {"count": v, "pct": pct(v, total)} for k, v in verdict_counter.items()},
        "top_blockers": blocker_counter.most_common(10),
        "confidence_distribution": dict(confidence_counter),
        "category_analysis": category_analysis,
        "key_findings": key_findings,
    }

    PATTERNS_FILE.write_text(json.dumps(patterns, indent=2))
    print(f"Wrote {PATTERNS_FILE} from {total}/100 researched apps.")
    for f in key_findings:
        print(f"  - {f}")


if __name__ == "__main__":
    main()

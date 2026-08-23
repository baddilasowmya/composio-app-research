"""
Verification pass
--------------------
Samples N apps (fixed random seed -> reproducible sample) from results.json,
re-checks each field against its OWN cited evidence URL (field-level, not
one blob of URLs), and records a per-field audit trail:

  {app, field, agent_claim, source, verdict, reason, corrected_value}

Writes data/verification.json with the full audit trail plus a summary
(initial accuracy, corrections applied, final accuracy).

Uses groq/compound (same backend as research_agent.py) so the whole
pipeline runs on one free API key.

Usage:
  python verify_sample.py --n 25 --seed 42
  python verify_sample.py --n 25 --apply-corrections   # writes corrected_value back into results.json
"""

import argparse
import json
import os
import random
import re
from pathlib import Path

from groq import Groq

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"
VERIFY_FILE = DATA_DIR / "verification.json"

MODEL = "groq/compound"
FIELDS_TO_CHECK = ["auth_methods", "credential_access", "api_protocols", "buildability_verdict"]

VERIFY_SYSTEM = """You are a fact-checker. You'll get one claimed field value for an app and the
URL(s) cited as evidence for it. Search the web / visit the URL(s) and determine whether the
source actually supports the claim.

Respond ONLY with JSON, no markdown fences:
{
  "verdict": "CORRECT" | "INCORRECT" | "UNVERIFIABLE",
  "reason": "1-2 sentences",
  "corrected_value": null or the correct value if INCORRECT
}
Be strict: UNVERIFIABLE if the page is unreachable or genuinely ambiguous, not a coin flip.
"""


def verify_field(client, app_name, field, claim, urls):
    prompt = f"App: {app_name}\nField: {field}\nClaimed value: {json.dumps(claim)}\nEvidence URL(s): {urls}"
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": VERIFY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        compound_custom={"tools": {"enabled_tools": ["web_search", "visit_website"]}},
        temperature=0.1,
        max_completion_tokens=500,
    )
    raw = response.choices[0].message.content or "{}"
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"verdict": "UNVERIFIABLE", "reason": "could not parse fact-checker output", "corrected_value": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=25, help="sample size (default 25, per assignment guidance of 20-30)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for a reproducible sample")
    parser.add_argument("--apply-corrections", action="store_true", help="write corrected_value back into results.json")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("Set GROQ_API_KEY in your environment before running.")
    client = Groq(api_key=api_key, default_headers={"Groq-Model-Version": "latest"})

    results = json.loads(RESULTS_FILE.read_text())
    if len(results) < args.n:
        print(f"NOTE: only {len(results)} results exist, sampling all of them (requested {args.n}).")

    rng = random.Random(args.seed)
    sample = rng.sample(results, min(args.n, len(results)))

    audit = []
    correct = incorrect = unverifiable = 0

    for record in sample:
        for field in FIELDS_TO_CHECK:
            claim = record.get(field)
            urls = (record.get("evidence") or {}).get(field) or (record.get("evidence") or {}).get("buildability", [])
            if not claim or not urls:
                continue
            v = verify_field(client, record["app"], field, claim, urls)
            entry = {
                "app": record["app"],
                "id": record["id"],
                "field": field,
                "agent_claim": claim,
                "source": urls,
                "verdict": v.get("verdict", "UNVERIFIABLE"),
                "reason": v.get("reason", ""),
                "corrected_value": v.get("corrected_value"),
            }
            audit.append(entry)
            if entry["verdict"] == "CORRECT":
                correct += 1
            elif entry["verdict"] == "INCORRECT":
                incorrect += 1
            else:
                unverifiable += 1
            print(f"{record['app']} / {field}: {entry['verdict']}")
            VERIFY_FILE.write_text(json.dumps({"audit": audit}, indent=2))

    total = correct + incorrect + unverifiable
    initial_accuracy = round(100 * correct / total, 1) if total else 0.0

    corrections_applied = 0
    if args.apply_corrections:
        by_id = {r["id"]: r for r in results}
        for entry in audit:
            if entry["verdict"] == "INCORRECT" and entry["corrected_value"] is not None:
                by_id[entry["id"]][entry["field"]] = entry["corrected_value"]
                by_id[entry["id"]]["human_corrected"] = True
                corrections_applied += 1
        RESULTS_FILE.write_text(json.dumps(sorted(by_id.values(), key=lambda r: r["id"]), indent=2))

    final_accuracy = round(100 * (correct + corrections_applied) / total, 1) if total else 0.0

    summary = {
        "sample_size": len(sample),
        "sample_pct_of_total": round(100 * len(sample) / len(results), 1) if results else 0,
        "seed": args.seed,
        "fields_checked": total,
        "correct": correct,
        "incorrect": incorrect,
        "unverifiable": unverifiable,
        "initial_accuracy_pct": initial_accuracy,
        "corrections_applied": corrections_applied,
        "final_accuracy_pct": final_accuracy if args.apply_corrections else None,
    }
    VERIFY_FILE.write_text(json.dumps({"summary": summary, "audit": audit}, indent=2))

    print(f"\nSampled {len(sample)}/{len(results)} apps ({summary['sample_pct_of_total']}%), seed={args.seed}")
    print(f"Fields checked: {total} | correct: {correct} | incorrect: {incorrect} | unverifiable: {unverifiable}")
    print(f"Initial accuracy: {initial_accuracy}%")
    if args.apply_corrections:
        print(f"Corrections applied: {corrections_applied} | Final accuracy: {final_accuracy}%")
    else:
        print("Run again with --apply-corrections to write fixes back into results.json")


if __name__ == "__main__":
    main()

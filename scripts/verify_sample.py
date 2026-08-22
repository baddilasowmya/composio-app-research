"""
Verification pass.

Takes a random sample of N apps from data/results.json, re-checks each
field against the cited evidence_urls (by fetching the page and asking
the model to confirm/deny each claim), and writes data/verification.json
with per-field agree/disagree + notes. This is what lets the final
report say "we sampled X%, Y were correct" with real numbers instead
of a made-up accuracy claim.

Run after research_agent.py has produced results.json.
Usage: python verify_sample.py --n 20
"""

import argparse
import json
import os
import random
import re
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"
VERIFY_FILE = DATA_DIR / "verification.json"

MODEL = "claude-sonnet-4-6"

VERIFY_SYSTEM = """You are a fact-checker. You will be given a research claim about
an app's API/auth and a set of evidence URLs. Fetch the URLs and determine whether
each field of the claim is CORRECT, INCORRECT, or UNVERIFIABLE (page unreachable /
doesn't confirm either way). Be strict - only mark CORRECT if the source actually
supports it.

Respond ONLY with JSON:
{
  "auth_methods_verdict": "CORRECT/INCORRECT/UNVERIFIABLE",
  "self_serve_verdict": "CORRECT/INCORRECT/UNVERIFIABLE",
  "api_surface_verdict": "CORRECT/INCORRECT/UNVERIFIABLE",
  "mcp_exists_verdict": "CORRECT/INCORRECT/UNVERIFIABLE",
  "notes": "1-2 sentence explanation of any disagreement"
}
"""


def verify_record(client, record):
    prompt = f"""Claim about {record['app']}:
auth_methods: {record.get('auth_methods')}
self_serve: {record.get('self_serve')}
api_surface: {record.get('api_surface')}
mcp_exists: {record.get('mcp_exists')}

Evidence URLs to check: {record.get('evidence_urls')}
"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=VERIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    raw = text_blocks[-1] if text_blocks else "{}"
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "parse_error", "raw": raw}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20, help="sample size")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in your environment before running.")

    client = anthropic.Anthropic(api_key=api_key)
    results = json.loads(RESULTS_FILE.read_text())
    sample = random.sample(results, min(args.n, len(results)))

    verification = []
    for record in sample:
        v = verify_record(client, record)
        v["id"] = record["id"]
        v["app"] = record["app"]
        verification.append(v)
        print(f"Verified {record['app']}: {v}")
        VERIFY_FILE.write_text(json.dumps(verification, indent=2))

    # summary
    total_fields = 0
    correct_fields = 0
    for v in verification:
        for k in ["auth_methods_verdict", "self_serve_verdict", "api_surface_verdict", "mcp_exists_verdict"]:
            if k in v:
                total_fields += 1
                if v[k] == "CORRECT":
                    correct_fields += 1

    accuracy = round(100 * correct_fields / total_fields, 1) if total_fields else 0
    print(f"\nSampled {len(sample)}/{len(results)} apps ({round(100*len(sample)/len(results),1)}%).")
    print(f"Field-level accuracy: {correct_fields}/{total_fields} = {accuracy}%")


if __name__ == "__main__":
    main()

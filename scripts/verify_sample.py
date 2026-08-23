"""
Verification pass
--------------------
Samples N apps (fixed random seed -> reproducible sample) from results.json,
re-checks each field against its OWN cited evidence URL (field-level, not
one blob of URLs) by re-fetching that URL directly, and records a per-field
audit trail: {app, field, agent_claim, source, verdict, reason, corrected_value}.

Writes data/verification.json with the full audit trail plus a summary
(initial accuracy, corrections applied, final accuracy).

Provider-flexible: uses whichever of GROQ_API_KEY / ANTHROPIC_API_KEY /
OPENROUTER_API_KEY is set, same as research_agent.py.

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

import requests

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"
VERIFY_FILE = DATA_DIR / "verification.json"

FIELDS_TO_CHECK = ["auth_methods", "credential_access", "api_protocols", "buildability_verdict"]
FETCH_TIMEOUT = 12
FETCH_MAX_CHARS = 6000
USER_AGENT = "Mozilla/5.0 (compatible; ComposioResearchAgent/1.0)"

VERIFY_SYSTEM = """You are a fact-checker. You'll get one claimed field value for an app and the
page text fetched from its cited evidence URL. Determine whether that page text actually
supports the claim.

Respond ONLY with JSON, no markdown fences:
{
  "verdict": "CORRECT" | "INCORRECT" | "UNVERIFIABLE",
  "reason": "1-2 sentences",
  "corrected_value": null or the correct value if INCORRECT
}
Be strict: UNVERIFIABLE if the page text doesn't clearly confirm or deny the claim, not a coin flip.
"""


def fetch_page_text(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
        if resp.status_code >= 400:
            return None
        if BS4_AVAILABLE:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
        else:
            text = re.sub(r"<[^>]+>", " ", resp.text)
        return text[:FETCH_MAX_CHARS]
    except requests.RequestException:
        return None


class LLMProvider:
    def __init__(self):
        if os.environ.get("GROQ_API_KEY"):
            self.kind = "groq"
            from groq import Groq
            self.client = Groq(api_key=os.environ["GROQ_API_KEY"], default_headers={"Groq-Model-Version": "latest"})
            self.model = "openai/gpt-oss-120b"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self.kind = "anthropic"
            import anthropic
            self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            self.model = "claude-sonnet-4-6"
        elif os.environ.get("OPENROUTER_API_KEY"):
            self.kind = "openrouter"
            from openai import OpenAI
            self.client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1")
            self.model = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
        else:
            raise SystemExit("No LLM key found. Set ONE of: GROQ_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY.")
        print(f"Using provider: {self.kind} (model: {self.model})")

    def complete(self, user_prompt):
        if self.kind == "groq":
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": VERIFY_SYSTEM}, {"role": "user", "content": user_prompt}],
                temperature=0.1, max_completion_tokens=400,
            )
            return resp.choices[0].message.content or "{}"
        if self.kind == "anthropic":
            resp = self.client.messages.create(
                model=self.model, max_tokens=400, system=VERIFY_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
            blocks = [b.text for b in resp.content if b.type == "text"]
            return blocks[-1] if blocks else "{}"
        if self.kind == "openrouter":
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": VERIFY_SYSTEM}, {"role": "user", "content": user_prompt}],
                temperature=0.1, max_tokens=400,
            )
            return resp.choices[0].message.content or "{}"


def verify_field(provider, app_name, field, claim, urls):
    page_texts = []
    for u in urls:
        text = fetch_page_text(u)
        if text:
            page_texts.append(f"--- {u} ---\n{text}")
    context = "\n\n".join(page_texts) if page_texts else "(could not fetch any cited URL)"

    prompt = f"App: {app_name}\nField: {field}\nClaimed value: {json.dumps(claim)}\n\nFetched page text from cited source(s):\n{context}"
    raw = provider.complete(prompt)
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"verdict": "UNVERIFIABLE", "reason": "could not parse fact-checker output", "corrected_value": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--apply-corrections", action="store_true")
    args = parser.parse_args()

    provider = LLMProvider()
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
            v = verify_field(provider, record["app"], field, claim, urls)
            entry = {
                "app": record["app"], "id": record["id"], "field": field,
                "agent_claim": claim, "source": urls,
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
        "seed": args.seed, "fields_checked": total,
        "correct": correct, "incorrect": incorrect, "unverifiable": unverifiable,
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

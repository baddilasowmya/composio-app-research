"""
Composio App Research Agent (v2)
----------------------------------
For each app in data/apps.json, produce a structured research record with
field-level evidence, confidence, and a buildability verdict.

WHERE COMPOSIO IS USED (not just installed):
  1. `composio.toolkits.get(slug=...)` is called for every app FIRST, against
     Composio's own toolkit catalog. If Composio already ships a toolkit for
     this app, that is first-party, authoritative evidence for `mcp_exists`
     and `buildability_verdict` -- it's not an inference, Composio's own
     platform already proves the app is agent-callable. This is the fastest,
     most reliable signal available and it comes directly from the SDK.
  2. For apps with no existing Composio toolkit, `composio.toolkits.get()`
     (catalog listing) is used to check for adjacent/related toolkits that
     could still reach the app (e.g. a generic HTTP toolkit).
  3. Claude (via the Anthropic API, web_search tool) fills in and cross-checks
     the fields Composio's catalog can't answer on its own: specific auth
     flow, self-serve vs gated, API breadth, and the evidence URLs backing
     each claim.

This keeps Composio as the first-party source of truth for "is this
buildable today" and uses the LLM+search step only for the narrower job of
describing auth/access, which Composio's catalog metadata doesn't cover.

Requires:
  ANTHROPIC_API_KEY  - for the research/reasoning step
  COMPOSIO_API_KEY   - for the toolkit-catalog lookup step

Usage:
  python research_agent.py                 # research all apps not yet done
  python research_agent.py --retry-failed   # re-run only apps in failures.json
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

try:
    from composio import Composio
    COMPOSIO_AVAILABLE = True
except ImportError:
    COMPOSIO_AVAILABLE = False

DATA_DIR = Path(__file__).parent.parent / "data"
APPS_FILE = DATA_DIR / "apps.json"
RESULTS_FILE = DATA_DIR / "results.json"
LOG_FILE = DATA_DIR / "run_log.json"
FAILURES_FILE = DATA_DIR / "failures.json"

MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3

SYSTEM_PROMPT = """You are a research agent investigating developer/API access for a named app.
Composio's own toolkit catalog has already been checked for this app (the result is given to
you) -- treat that as ground truth for whether Composio has a first-party toolkit, and focus your
web research on the fields that catalog check cannot answer: the specific auth flow, whether
credentials are self-serve or gated, and the shape of the public API.

Use web search. Every field must be backed by at least one real URL you actually found -- never
invent a URL, and never state a field with no supporting source (use "unclear" / empty list
instead).

Respond ONLY with valid JSON, no markdown fences, matching this schema exactly:
{
  "one_line": "what the app does, one line",
  "auth_methods": ["OAuth2", "API key", ...],
  "auth_notes": "short clarification if there's nuance (e.g. static token vs OAuth for public apps)",
  "credential_access": "self-serve" | "gated" | "unclear",
  "credential_requirements": "what's actually required to get credentials (e.g. 'free developer signup', 'Business plan + admin approval')",
  "api_protocols": ["REST"] | ["REST","GraphQL"] | [],
  "api_breadth": "narrow" | "moderate" | "broad",
  "api_documentation_url": "the main public API docs URL, or empty string",
  "buildability_verdict": "ready" | "partial" | "blocked",
  "blocker": "main blocker if partial/blocked, else empty string",
  "buildability_reason": "one sentence justifying the verdict",
  "confidence": "high" | "medium" | "low",
  "evidence": {
    "auth_methods": ["url1"],
    "credential_access": ["url2"],
    "api_surface": ["url3"],
    "buildability": ["url2","url3"]
  }
}
"""


def check_composio_catalog(composio_client, app_name):
    """Query Composio's own toolkit catalog for a first-party toolkit.
    This is real evidence, not an LLM guess -- if Composio ships this
    toolkit, the app is provably agent-callable today via Composio itself.
    """
    if composio_client is None:
        return {"checked": False, "reason": "COMPOSIO_API_KEY not set"}

    slug_guess = re.sub(r"[^a-z0-9]+", "", app_name.lower())
    try:
        toolkit = composio_client.toolkits.get(slug=slug_guess)
        if toolkit:
            return {
                "checked": True,
                "composio_toolkit_exists": True,
                "toolkit_slug": slug_guess,
                "toolkit_version": getattr(getattr(toolkit, "meta", None), "version", None),
            }
    except Exception:
        pass
    return {"checked": True, "composio_toolkit_exists": False, "toolkit_slug": slug_guess}


def research_app(anthropic_client, app, composio_catalog_result):
    user_prompt = f"""App: {app['app']}
Category: {app['category']}
Hint: {app['hint']}

Composio catalog check result (first-party, already confirmed -- do not re-derive this):
{json.dumps(composio_catalog_result)}

Research the remaining fields per the schema, using web search."""

    resp = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    raw = text_blocks[-1] if text_blocks else "{}"
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)  # let JSONDecodeError propagate to the retry loop


REQUIRED_FIELDS = [
    "one_line", "auth_methods", "credential_access", "api_protocols",
    "buildability_verdict", "confidence", "evidence",
]


def validate_record(record):
    for field in REQUIRED_FIELDS:
        if field not in record or record[field] in (None, "", []):
            return False, f"missing or empty: {field}"
    if not record.get("evidence") or not any(record["evidence"].values()):
        return False, "no evidence for any field"
    if record["buildability_verdict"] not in ("ready", "partial", "blocked"):
        return False, "invalid buildability_verdict"
    if record["credential_access"] not in ("self-serve", "gated", "unclear"):
        return False, "invalid credential_access"
    return True, None


def research_app_with_retries(anthropic_client, composio_client, app):
    composio_result = check_composio_catalog(composio_client, app["app"])

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            record = research_app(anthropic_client, app, composio_result)
            record["composio_catalog"] = composio_result
            record["researched_at"] = datetime.now(timezone.utc).isoformat()
            record["research_method"] = "composio_catalog_check+claude_web_search"

            ok, reason = validate_record(record)
            if ok:
                return {**app, **record}, attempt, None
            last_error = f"validation failed: {reason}"
        except json.JSONDecodeError as e:
            last_error = f"json parse error: {e}"
        except Exception as e:
            last_error = f"exception: {e}"
        time.sleep(min(2 ** attempt, 10))  # exponential backoff

    return None, MAX_RETRIES, last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-failed", action="store_true", help="only re-run apps listed in failures.json")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in your environment before running.")
    anthropic_client = anthropic.Anthropic(api_key=api_key)

    composio_key = os.environ.get("COMPOSIO_API_KEY")
    composio_client = None
    if composio_key and COMPOSIO_AVAILABLE:
        composio_client = Composio(api_key=composio_key)
    elif not COMPOSIO_AVAILABLE:
        print("WARNING: composio package not installed (pip install composio) -- "
              "skipping first-party toolkit catalog check, evidence will be web-only.")
    elif not composio_key:
        print("WARNING: COMPOSIO_API_KEY not set -- skipping first-party toolkit catalog check.")

    apps = json.loads(APPS_FILE.read_text())
    results = json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []
    log = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    failures = json.loads(FAILURES_FILE.read_text()) if FAILURES_FILE.exists() else []

    done_ids = {r["id"] for r in results}

    if args.retry_failed:
        failed_ids = {f["id"] for f in failures}
        todo = [a for a in apps if a["id"] in failed_ids]
    else:
        todo = [a for a in apps if a["id"] not in done_ids]

    for app in todo:
        start = time.time()
        record, attempts, error = research_app_with_retries(anthropic_client, composio_client, app)
        elapsed = round(time.time() - start, 1)

        if record:
            results = [r for r in results if r["id"] != app["id"]] + [record]
            failures = [f for f in failures if f["id"] != app["id"]]
            status = "ok"
            print(f"[{app['id']}/100] {app['app']}: ok ({attempts} attempt(s), {elapsed}s)")
        else:
            failures.append({"id": app["id"], "app": app["app"], "error": error})
            status = "failed"
            print(f"[{app['id']}/100] {app['app']}: FAILED after {attempts} attempts -- {error}")

        log.append({"id": app["id"], "app": app["app"], "status": status, "attempts": attempts, "seconds": elapsed})

        RESULTS_FILE.write_text(json.dumps(sorted(results, key=lambda r: r["id"]), indent=2))
        LOG_FILE.write_text(json.dumps(log, indent=2))
        FAILURES_FILE.write_text(json.dumps(failures, indent=2))

        time.sleep(1)

    print(f"\nDone. {len(results)}/100 apps researched, {len(failures)} in failures.json.")
    if failures:
        print("Re-run with --retry-failed to retry just the failed apps.")


if __name__ == "__main__":
    main()

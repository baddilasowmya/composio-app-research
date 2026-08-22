"""
Composio App Research Agent
----------------------------
Automates the research checklist for the take-home assignment:
for each app in data/apps.json, find:
  - category + one-line description
  - auth method(s)
  - self-serve vs gated credentials
  - API surface (REST/GraphQL, MCP existing?)
  - buildability verdict + blocker
  - evidence URL(s)

Requires an ANTHROPIC_API_KEY (Claude + server-side web_search tool)
in the environment. Run: python research_agent.py

Output: data/results.json (one record per app, each field backed by
a source URL) and data/run_log.json (per-app pass/fail + timing, so
the verification step and README can report real numbers, not
invented ones).
"""

import json
import os
import time
import re
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).parent.parent / "data"
APPS_FILE = DATA_DIR / "apps.json"
RESULTS_FILE = DATA_DIR / "results.json"
LOG_FILE = DATA_DIR / "run_log.json"

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a research agent. For the given app, use web search to find:
1. category (given, do not change)
2. one_line: what the app does, in one line
3. auth_methods: list of auth methods supported (OAuth2, API key, Basic, token, other) - be specific
4. self_serve: "self-serve" (free/trial signup gets API credentials), "gated" (needs paid plan/admin approval/partner status), or "unclear"
5. api_surface: brief description of the public API (REST/GraphQL, roughly how broad) and whether it's documented publicly
6. mcp_exists: true/false/unclear - whether an official or well-known MCP server already exists for this app
7. buildability_verdict: "ready" (agent toolkit buildable today), "blocked" (explain main blocker), or "partial"
8. blocker: if blocked or partial, the main blocker (e.g. "requires partner approval", "no public API", "OAuth app review needed"). Empty string if ready.
9. evidence_urls: list of 1-3 URLs that back up the above answers (must be real URLs you found via search, not invented)

Respond ONLY with valid JSON matching this schema, no markdown fences, no preamble:
{
  "one_line": "...",
  "auth_methods": ["..."],
  "self_serve": "...",
  "api_surface": "...",
  "mcp_exists": true/false/"unclear",
  "buildability_verdict": "...",
  "blocker": "...",
  "evidence_urls": ["..."]
}
"""


def research_app(client, app):
    user_prompt = f"App: {app['app']}\nCategory: {app['category']}\nHint: {app['hint']}\n\nResearch this app's developer/API access per the schema."
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    # Pull the final text block (the JSON answer) out of the response
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    raw = text_blocks[-1] if text_blocks else "{}"
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"error": "could not parse model output", "raw": raw}
    return parsed


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in your environment before running.")

    client = anthropic.Anthropic(api_key=api_key)
    apps = json.loads(APPS_FILE.read_text())

    results = []
    log = []

    if RESULTS_FILE.exists():
        results = json.loads(RESULTS_FILE.read_text())
    done_ids = {r["id"] for r in results}

    for app in apps:
        if app["id"] in done_ids:
            continue
        start = time.time()
        try:
            research = research_app(client, app)
            status = "ok" if "error" not in research else "parse_error"
        except Exception as e:
            research = {"error": str(e)}
            status = "exception"
        elapsed = round(time.time() - start, 1)

        record = {**app, **research}
        results.append(record)
        log.append({"id": app["id"], "app": app["app"], "status": status, "seconds": elapsed})

        print(f"[{app['id']}/100] {app['app']}: {status} ({elapsed}s)")

        # save incrementally so a crash doesn't lose progress
        RESULTS_FILE.write_text(json.dumps(results, indent=2))
        LOG_FILE.write_text(json.dumps(log, indent=2))

        time.sleep(1)  # be polite to rate limits

    print(f"\nDone. {len(results)}/100 apps researched. See {RESULTS_FILE}")


if __name__ == "__main__":
    main()

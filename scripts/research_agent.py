"""
Composio App Research Agent
------------------------------
For each app in data/apps.json, produce a structured research record with
field-level evidence, confidence, and a buildability verdict.

DESIGN: fetch the real page first, ask the model second.
  Every app in apps.json already has a "hint" -- a known docs/website URL.
  Rather than paying an LLM's web-search tool to go rediscover that same
  page, this agent fetches it directly (plain HTTP, no API key needed) and
  gives the model that actual page text to extract structured fields from.
  This is cheaper, faster, and produces tighter evidence -- the URL cited
  IS the URL that was actually read, not a search result the model chose.

  If the direct fetch fails (dead link, blocked, JS-only page, thin
  content), the agent falls back to the provider's own web-search tool
  for that one app, when the provider supports it.

PROVIDER-FLEXIBLE: works with whichever key you have.
  Checked in this order: GROQ_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY.
  Whichever is set gets used automatically -- no code changes needed to
  switch providers. Search-fallback support varies by provider (see
  LLMProvider.complete below); extraction-from-fetched-text works
  identically on all three.

COMPOSIO: `composio.toolkits.get(slug=...)` is checked for every app first,
  against Composio's own toolkit catalog -- first-party evidence, not an
  LLM guess, for whether the app is already agent-callable.

Requires ONE of:
  GROQ_API_KEY        (free tier: console.groq.com)
  ANTHROPIC_API_KEY    (console.anthropic.com)
  OPENROUTER_API_KEY   (openrouter.ai -- many free/cheap models)
Optional:
  COMPOSIO_API_KEY     (for the first-party toolkit catalog check)

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

import requests

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

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

MAX_RETRIES = 4
FETCH_TIMEOUT = 12
FETCH_MAX_CHARS = 8000  # keep the page text a model can digest cheaply
USER_AGENT = "Mozilla/5.0 (compatible; ComposioResearchAgent/1.0; +https://github.com/baddilasowmya/composio-app-research)"

SYSTEM_PROMPT = """You are a research agent investigating developer/API access for a named app.

IMPORTANT RESEARCH RULES:
- Base your answer on the page text given to you. Do not invent facts not supported by it.
- Never invent an evidence URL -- only cite the URL you were actually given, or one you found
  yourself if you were told to search.
- If a fact isn't covered by what you were given, use "unclear" (or an empty list/string)
  rather than guessing.
- Composio's own toolkit catalog has already been checked for this app (given to you in the
  prompt) -- treat that as ground truth for whether Composio has a first-party toolkit already;
  don't re-derive it.
- Respond ONLY with valid JSON, no markdown fences, no preamble.

Schema:
{
  "one_line": "what the app does, one line",
  "auth_methods": ["OAuth2", "API key", ...],
  "auth_notes": "short clarification if there's nuance",
  "credential_access": "self-serve" | "gated" | "unclear",
  "credential_requirements": "what's actually required to get credentials",
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


# ---------------------------------------------------------------------------
# Step 1: fetch the known docs/hint URL directly -- no API key needed for this.
# ---------------------------------------------------------------------------

def normalize_hint_url(hint):
    """apps.json 'hint' values are often bare domains or have trailing notes
    in parens, e.g. 'zoho.com/crm' or 'fanbasis.com'. Turn them into a URL."""
    url_part = hint.split(" (")[0].strip()
    if not url_part.startswith("http"):
        url_part = "https://" + url_part
    return url_part


def fetch_page_text(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        if BS4_AVAILABLE:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
        else:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s{2,}", " ", text)
        text = text[:FETCH_MAX_CHARS]
        if len(text) < 200:
            return None, "page fetched but too thin (likely JS-rendered)"
        return text, None
    except requests.RequestException as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Step 2: provider-flexible LLM call. Works with whichever key is set.
# ---------------------------------------------------------------------------

class LLMProvider:
    def __init__(self):
        if os.environ.get("GROQ_API_KEY"):
            self.kind = "groq"
            from groq import Groq
            self.client = Groq(api_key=os.environ["GROQ_API_KEY"], default_headers={"Groq-Model-Version": "latest"})
            self.model = "openai/gpt-oss-120b"           # cheap/fast for extraction-from-text
            self.search_model = "groq/compound"           # only used for the search fallback
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
            raise SystemExit(
                "No LLM key found. Set ONE of: GROQ_API_KEY (free tier), "
                "ANTHROPIC_API_KEY, or OPENROUTER_API_KEY in your environment."
            )
        print(f"Using provider: {self.kind} (model: {self.model})")

    def complete(self, user_prompt, allow_search=False):
        """Returns raw text. allow_search=True only has an effect on providers
        with a native web-search tool (Groq compound, Anthropic web_search);
        OpenRouter free models generally don't have one, so search fallback
        for OpenRouter just relies on whatever the model already knows plus
        the (possibly failed) fetch context -- confidence should be marked
        accordingly by the model itself."""

        if self.kind == "groq":
            model = self.search_model if allow_search else self.model
            kwargs = {}
            if allow_search:
                kwargs["compound_custom"] = {"tools": {"enabled_tools": ["web_search", "visit_website"]}}
            resp = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                temperature=0.1,
                max_completion_tokens=1400,
                **kwargs,
            )
            return resp.choices[0].message.content or "{}"

        if self.kind == "anthropic":
            kwargs = {}
            if allow_search:
                kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                **kwargs,
            )
            text_blocks = [b.text for b in resp.content if b.type == "text"]
            return text_blocks[-1] if text_blocks else "{}"

        if self.kind == "openrouter":
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                temperature=0.1,
                max_tokens=1400,
            )
            return resp.choices[0].message.content or "{}"


def check_composio_catalog(composio_client, app_name):
    if composio_client is None:
        return {"checked": False, "reason": "COMPOSIO_API_KEY not set"}
    slug_guess = re.sub(r"[^a-z0-9]+", "", app_name.lower())
    try:
        toolkit = composio_client.toolkits.get(slug=slug_guess)
        if toolkit:
            return {"checked": True, "composio_toolkit_exists": True, "toolkit_slug": slug_guess,
                     "toolkit_version": getattr(getattr(toolkit, "meta", None), "version", None)}
    except Exception:
        pass
    return {"checked": True, "composio_toolkit_exists": False, "toolkit_slug": slug_guess}


def research_app(provider, app, composio_catalog_result):
    hint_url = normalize_hint_url(app["hint"])
    page_text, fetch_error = fetch_page_text(hint_url)

    if page_text:
        user_prompt = f"""App: {app['app']}
Category: {app['category']}

Composio catalog check result (first-party, already confirmed):
{json.dumps(composio_catalog_result)}

Page text fetched directly from {hint_url}:
---
{page_text}
---
Extract the schema fields from this page text. Cite {hint_url} as evidence for anything it
supports. If the page doesn't cover a field, mark it unclear rather than guessing."""
        raw = provider.complete(user_prompt, allow_search=False)
    else:
        # fallback: direct fetch failed, let the provider search instead (if it can)
        user_prompt = f"""App: {app['app']}
Category: {app['category']}
Hint: {app['hint']}

Direct fetch of {hint_url} failed ({fetch_error}). Use web search to find the real developer/
API docs for this app and extract the schema fields, citing real URLs you actually find.

Composio catalog check result (first-party, already confirmed):
{json.dumps(composio_catalog_result)}
"""
        raw = provider.complete(user_prompt, allow_search=True)

    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(raw)  # let JSONDecodeError propagate to the retry loop
    parsed["_fetch_method"] = "direct_fetch" if page_text else f"search_fallback ({fetch_error})"
    return parsed


REQUIRED_FIELDS = ["one_line", "auth_methods", "credential_access", "api_protocols",
                    "buildability_verdict", "confidence", "evidence"]


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


def research_app_with_retries(provider, composio_client, app):
    composio_result = check_composio_catalog(composio_client, app["app"])
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            record = research_app(provider, app, composio_result)
            record["composio_catalog"] = composio_result
            record["researched_at"] = datetime.now(timezone.utc).isoformat()
            record["research_method"] = f"{provider.kind}:{record.pop('_fetch_method', 'unknown')}"

            ok, reason = validate_record(record)
            if ok:
                return {**app, **record}, attempt, None
            last_error = f"validation failed: {reason}"
            time.sleep(min(2 ** attempt, 10))
        except json.JSONDecodeError as e:
            last_error = f"json parse error: {e}"
            time.sleep(min(2 ** attempt, 10))
        except Exception as e:
            last_error = f"exception: {e}"
            if "rate_limit" in str(e).lower() or "429" in str(e):
                print(f"    rate limited -- waiting 65s for the per-minute budget to reset...")
                time.sleep(65)
            else:
                time.sleep(min(2 ** attempt, 10))

    return None, MAX_RETRIES, last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-failed", action="store_true", help="only re-run apps listed in failures.json")
    args = parser.parse_args()

    provider = LLMProvider()

    composio_key = os.environ.get("COMPOSIO_API_KEY")
    composio_client = None
    if composio_key and COMPOSIO_AVAILABLE:
        composio_client = Composio(api_key=composio_key)
    elif not composio_key:
        print("NOTE: COMPOSIO_API_KEY not set -- skipping first-party toolkit catalog check "
              "(optional, everything else still works).")

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
        record, attempts, error = research_app_with_retries(provider, composio_client, app)
        elapsed = round(time.time() - start, 1)

        if record:
            results = [r for r in results if r["id"] != app["id"]] + [record]
            failures = [f for f in failures if f["id"] != app["id"]]
            status = "ok"
            print(f"[{app['id']}/100] {app['app']}: ok ({record.get('research_method','?')}, {attempts} attempt(s), {elapsed}s)")
        else:
            failures.append({"id": app["id"], "app": app["app"], "error": error})
            status = "failed"
            print(f"[{app['id']}/100] {app['app']}: FAILED after {attempts} attempts -- {error}")

        log.append({"id": app["id"], "app": app["app"], "status": status, "attempts": attempts, "seconds": elapsed})

        RESULTS_FILE.write_text(json.dumps(sorted(results, key=lambda r: r["id"]), indent=2))
        LOG_FILE.write_text(json.dumps(log, indent=2))
        FAILURES_FILE.write_text(json.dumps(failures, indent=2))

        time.sleep(1.5)

    print(f"\nDone. {len(results)}/100 apps researched, {len(failures)} in failures.json.")
    if failures:
        print("Re-run with --retry-failed to retry just the failed apps.")


if __name__ == "__main__":
    main()

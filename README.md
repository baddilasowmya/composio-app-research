# Composio App Research — AI Product Ops take-home

🟢 **Repo:** you're in it.
🔵 **Live dashboard:** enable GitHub Pages (Settings → Pages → branch `main`, folder `/docs`) to get `https://<username>.github.io/composio-app-research/` — until then, open `docs/index.html` directly (see "Viewing the dashboard" below).

## Current status — read this first

**4 of 100 apps are fully, genuinely researched** (Salesforce, HubSpot, Pipedrive,
Attio — Category 1, CRM and Sales), each with real citations checked by hand.
**The other 96 are queued**, not faked — `data/results.json` only contains
records that actually went through research, and `scripts/validate_results.py`
enforces that: it fails loudly if any record is missing fields, has an
unresolved error, or lacks evidence, and it reports current coverage honestly
rather than claiming 100/100 when it isn't.

Reaching 100/100 requires running `scripts/research_agent.py` with **any one**
API key (see **Running it** below — it works with Groq, Anthropic, or
OpenRouter, whichever you have). That takes roughly 10–20 minutes once you
have a key.

## What this is

For each of the 100 apps in Composio's research set: category, auth method(s),
self-serve vs. gated credential access, API surface, whether Composio already
has a first-party toolkit for it, and a buildability verdict — every field
backed by a real source URL, with a verification pass that fact-checks a
sample against those sources and reports honest before/after accuracy.

## How it works

```
100 apps (data/apps.json) -- each already has a known docs/website URL ("hint")
        ↓
Composio toolkit-catalog check   ← composio.toolkits.get(slug=...), first-party evidence
        ↓
Fetch that app's hint URL directly (plain HTTP, no API key needed)
        ↓
   ┌─ fetch succeeded ──────────────┐   ┌─ fetch failed/blocked/JS-only ─┐
   │ LLM extracts schema fields      │   │ LLM falls back to its own web  │
   │ from the actual fetched text    │   │ search tool for that one app   │
   │ (cheap call, no search needed)  │   │ (Groq/Anthropic only — see     │
   └──────────────┬───────────────────┘   │ provider notes below)          │
                  │                       └────────────────┬────────────────┘
                  └──────────────┬────────────────────────┘
        ↓
Schema + evidence validation (retries on failure, exponential backoff,
rate-limit-aware waits)
        ↓
data/results.json (incremental — safe to stop/resume)
        ↓
Verification: sample N results, re-fetch each field's own cited URL, ask the
LLM to confirm CORRECT / INCORRECT / UNVERIFIABLE, apply corrections
        ↓
data/verification.json (audit trail + before/after accuracy)
        ↓
Pattern analysis over the corrected data → data/patterns.json
        ↓
docs/index.html — reads results.json + patterns.json directly, no rebuild needed
```

**Why fetch-first instead of search-first:** every app in `apps.json` already
has a known docs/website URL. Paying an LLM's web-search tool to go
rediscover that same page is slower, costlier, and produces looser evidence
(the model picks *a* URL from search results, which may not be the one it
actually read). Fetching the known URL directly is free, and the evidence
cited is provably the page that was actually read. Search is used only as a
fallback, for the minority of apps where the direct fetch fails (dead link,
blocked, or a JS-only page with no static content).

## Provider-flexible — works with whatever key you have

`research_agent.py` and `verify_sample.py` both auto-detect which key is set
in your environment, in this order, and use that provider automatically —
**no code changes needed**:

| Env var | Provider | Search fallback support |
|---|---|---|
| `GROQ_API_KEY` | Groq (`llama-3.3-70b-versatile` for extraction, `groq/compound` for fallback search) | Yes — built-in |
| `ANTHROPIC_API_KEY` | Anthropic (`claude-sonnet-4-6`) | Yes — `web_search` tool |
| `OPENROUTER_API_KEY` | OpenRouter (any model, default a free Llama model — set `OPENROUTER_MODEL` to change it) | No native search — extraction-from-fetched-text still works identically; apps needing the search fallback will come back lower-confidence |

You only need **one** of these. If you have more than one key, Groq is tried
first (it has a free tier and the cheapest extraction path), then Anthropic,
then OpenRouter.

## Where Composio is actually used

Not just installed and left unused. `scripts/research_agent.py` calls
`composio.toolkits.get(slug=...)` against **Composio's own toolkit catalog**
for every app, *before* any web research happens. If Composio already ships a
toolkit for an app, that's first-party proof the app is agent-callable today —
authoritative evidence from Composio's own platform, not an LLM inference.
That result is fed into the research prompt so the model doesn't need to
re-derive what Composio's own SDK already knows; it only researches the
narrower fields the catalog can't answer (specific auth flow, self-serve vs.
gated, API breadth). This step is optional — set `COMPOSIO_API_KEY` to enable
it; without it, the agent still runs on the fetch/LLM pipeline alone.

## Research schema

Each record in `results.json`:

| Field | Meaning |
|---|---|
| `one_line` | what the app does |
| `auth_methods`, `auth_notes` | supported auth, with nuance (e.g. static token vs. OAuth for public apps) |
| `credential_access` | `self-serve` / `gated` / `unclear` |
| `credential_requirements` | what's actually needed to get credentials |
| `api_protocols`, `api_breadth`, `api_documentation_url` | shape of the public API |
| `composio_catalog` | first-party result of the Composio toolkit-catalog check |
| `buildability_verdict`, `blocker`, `buildability_reason` | `ready` / `partial` / `blocked`, and why |
| `confidence` | `high` / `medium` / `low` |
| `evidence` | **field-level**, e.g. `{"auth_methods": ["url"], "credential_access": ["url"]}` — not one undifferentiated URL list, so any claim traces to the specific source that backs it |
| `researched_at`, `research_method` | when, which provider, and `direct_fetch` vs. `search_fallback` |

## Verification methodology

`scripts/verify_sample.py` samples N apps (fixed seed → reproducible),
re-fetches each field's own cited URL directly, and asks the LLM to mark it
`CORRECT` / `INCORRECT` / `UNVERIFIABLE` against that freshly-fetched text —
writing a full per-field audit trail to `data/verification.json`, not just a
summary percentage. With `--apply-corrections`, confirmed errors are written
back into `results.json` and flagged `human_corrected: true`, and the script
reports both the initial and post-correction accuracy.

## Where a human is still needed

- Every agent-produced record that fails validation (`scripts/validate_results.py`)
  or gets flagged `INCORRECT` by verification is meant for manual review before
  it's trusted in the pattern analysis — the pipeline is built to *not* silently
  accept low-quality output.
- The 4 hand-researched apps set the ground-truth format the agent is asked to
  match; if the agent's output style drifts from that once it runs at scale,
  that's a signal to intervene, not something the pipeline hides.
- Apps where the direct fetch fails and the agent falls back to search (or, on
  OpenRouter, has no search fallback at all) are exactly where confidence
  should be lowest — worth a manual spot-check before trusting them fully.
- Ambiguous docs (e.g. an app with multiple auth paths depending on publish
  status, like Attio) got manual judgment calls that a fully automated pass
  might collapse into an oversimplified single label.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in ONE of GROQ_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY
export $(cat .env | xargs)   # Windows: set each var manually, see .env.example

python scripts/research_agent.py              # researches all apps not yet in results.json
python scripts/research_agent.py --retry-failed   # re-run only apps that failed validation

python scripts/validate_results.py             # quality gate — exits non-zero if anything's incomplete/invalid
python -m pytest tests/ -v                      # or: python tests/test_schema.py && python tests/test_urls.py

python scripts/verify_sample.py --n 25 --seed 42                     # sample + audit, no writes
python scripts/verify_sample.py --n 25 --seed 42 --apply-corrections  # writes fixes back into results.json

python scripts/analyze_patterns.py              # regenerate data/patterns.json from current results.json
```

All scripts write incrementally, so a crash or rate limit mid-run loses at
most the app in progress, not the whole batch — just re-run the same command
and it resumes.

## Viewing the dashboard

`docs/index.html` fetches `data/*.json` directly from GitHub's raw content
CDN (`raw.githubusercontent.com/.../main/data/...`), not from a relative
path — this is deliberate, since GitHub Pages only publishes the `/docs`
folder, so a relative `../data/...` path 404s once deployed even though it
works fine when testing locally with a full repo checkout. Because it fetches
from `main` directly, the live dashboard always reflects whatever is
currently pushed, with no separate build/deploy step for data changes.

**Live version:** enable GitHub Pages once (Settings → Pages → source:
`main` branch, folder `/docs`) → `https://<username>.github.io/<repo>/`.

**Local testing:** just open `docs/index.html` directly in a browser (no
local server needed) — it fetches the same raw GitHub URLs either way, so
local and deployed behave identically. (Requires the repo to be pushed to
GitHub already, since it reads from there, not from local disk.)

## Repo structure

```
data/
  apps.json          100-app input list, each with a category and a docs/website hint URL
  results.json       per-app research records (currently 4/100 real)
  failures.json       apps that failed validation after retries
  run_log.json        per-app execution log (status, attempts, timing)
  verification.json   sampled verification audit trail + accuracy
  patterns.json       computed cross-app patterns (regenerated from results.json)
scripts/
  research_agent.py    direct-fetch-first research, Composio catalog check, provider-flexible LLM
  verify_sample.py      field-level fact-check against re-fetched cited sources
  validate_results.py   quality gate / CI check
  analyze_patterns.py    pattern computation
tests/
  test_schema.py        required fields, enums, ID integrity
  test_urls.py           evidence URLs are well-formed and non-placeholder
docs/
  index.html             dashboard — reads results.json + patterns.json live
.github/workflows/
  validate.yml            CI: runs tests + quality gate on every push
```

## Limitations

- APIs and auth requirements change; `researched_at` timestamps every claim.
- OAuth/approval requirements can vary by account tier even within one app.
- "Buildable" means "publicly documented access existed at research time" —
  not a guarantee Composio has built or will build a toolkit for it (though
  the catalog check tells you where one already exists).
- Direct-fetch works for standard server-rendered docs pages; heavily
  JS-rendered sites (some marketing pages, some app dashboards) return too
  little static text and fall through to the search fallback instead.
- Third-party/community MCP servers, where mentioned, aren't vetted the way
  an official one is.
- OpenRouter's free-tier models have no built-in search — if the direct
  fetch fails for an app on that provider, the record will reflect lower
  confidence rather than a search-backed answer.
- Verification re-checks cited sources, not the full space of possible
  sources — an app could have a correct claim that its own evidence URL
  states poorly, or vice versa.

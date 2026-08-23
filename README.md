# Composio App Research — AI Product Ops take-home

🟢 **Repo:** you're in it.
🔵 **Live dashboard:** enable GitHub Pages (Settings → Pages → branch `main`, folder `/docs`) to get `https://<username>.github.io/composio-app-research/` — until then, open `docs/index.html` directly.

## Current status — read this first

**4 of 100 apps are fully, genuinely researched** (Salesforce, HubSpot, Pipedrive,
Attio — Category 1, CRM and Sales), each with real citations checked by hand.
**The other 96 are queued**, not faked — `data/results.json` only contains
records that actually went through research, and `scripts/validate_results.py`
enforces that: it fails loudly if any record is missing fields, has an
unresolved error, or lacks evidence, and it reports current coverage honestly
rather than claiming 100/100 when it isn't.

Reaching 100/100 requires running `scripts/research_agent.py` with a
`GROQ_API_KEY` (free tier is enough — and ideally a `COMPOSIO_API_KEY` too,
see below). That takes about 15–25 minutes once you have keys. See
**Running it** below.

## What this is

For each of the 100 apps in Composio's research set: category, auth method(s),
self-serve vs. gated credential access, API surface, whether Composio already
has a first-party toolkit for it, and a buildability verdict — every field
backed by a real source URL, with a verification pass that fact-checks a
sample against those sources and reports honest before/after accuracy.

## How it works

```
100 apps (data/apps.json)
        ↓
Composio toolkit-catalog check   ← composio.toolkits.get(slug=...), first-party evidence
        ↓
groq/compound + built-in web search fills remaining fields, cites a URL per field
        ↓
Schema + evidence validation (retries on failure, exponential backoff)
        ↓
data/results.json (incremental — safe to stop/resume)
        ↓
Verification: sample N results, re-check each field against its own cited
URL, log CORRECT / INCORRECT / UNVERIFIABLE, apply corrections
        ↓
data/verification.json (audit trail + before/after accuracy)
        ↓
Pattern analysis over the corrected data → data/patterns.json
        ↓
docs/index.html — reads results.json + patterns.json directly, no rebuild needed
```

## Where Composio is actually used

Not just installed and left unused. `scripts/research_agent.py` calls
`composio.toolkits.get(slug=...)` against **Composio's own toolkit catalog**
for every app, *before* any web research happens. If Composio already ships a
toolkit for an app, that's first-party proof the app is agent-callable today —
authoritative evidence from Composio's own platform, not an LLM inference.
That result is fed into the research prompt so the model doesn't need to
re-derive what Composio's own SDK already knows; it only researches the
narrower fields the catalog can't answer (specific auth flow, self-serve vs.
gated, API breadth). See the docstring at the top of `research_agent.py` for
the full breakdown.

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
| `researched_at`, `research_method` | when and how the record was produced |

## Verification methodology

`scripts/verify_sample.py` samples N apps (fixed seed → reproducible),
re-checks each field's claim against its own cited URL by re-fetching and
asking a fact-checking pass to mark it `CORRECT` / `INCORRECT` /
`UNVERIFIABLE`, and writes a full per-field audit trail to
`data/verification.json` — not just a summary percentage. With
`--apply-corrections`, confirmed errors are written back into `results.json`
and flagged `human_corrected: true`, and the script reports both the initial
and post-correction accuracy.

## Where a human is still needed

- Every agent-produced record that fails validation (`scripts/validate_results.py`)
  or gets flagged `INCORRECT` by verification is meant for manual review before
  it's trusted in the pattern analysis — the pipeline is built to *not* silently
  accept low-quality output.
- The 4 hand-researched apps set the ground-truth format the agent is asked to
  match; if the agent's output style drifts from that once it runs at scale,
  that's a signal to intervene, not something the pipeline hides.
- Ambiguous docs (e.g. an app with multiple auth paths depending on publish
  status, like Attio) got manual judgment calls that a fully automated pass
  might collapse into an oversimplified single label.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY (required, free tier) and COMPOSIO_API_KEY (recommended)
export $(cat .env | xargs)

python scripts/research_agent.py              # researches all apps not yet in results.json
python scripts/research_agent.py --retry-failed   # re-run only apps that failed validation

python scripts/validate_results.py             # quality gate — exits non-zero if anything's incomplete/invalid
python -m pytest tests/ -v                      # or: python tests/test_schema.py && python tests/test_urls.py

python scripts/verify_sample.py --n 25 --seed 42                     # sample + audit, no writes
python scripts/verify_sample.py --n 25 --seed 42 --apply-corrections  # writes fixes back into results.json

python scripts/analyze_patterns.py              # regenerate data/patterns.json from current results.json
```

All scripts write incrementally, so a crash or rate limit mid-run loses at
most the app in progress, not the whole batch.

## Repo structure

```
data/
  apps.json          100-app input list
  results.json       per-app research records (currently 4/100 real)
  failures.json       apps that failed validation after retries
  run_log.json        per-app execution log (status, attempts, timing)
  verification.json   sampled verification audit trail + accuracy
  patterns.json       computed cross-app patterns (regenerated from results.json)
scripts/
  research_agent.py    Composio catalog check + groq/compound web-search research
  verify_sample.py      field-level fact-check against cited sources
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
- Third-party/community MCP servers, where mentioned, aren't vetted the way
  an official one is.
- Verification re-checks cited sources, not the full space of possible
  sources — an app could have a correct claim that its own evidence URL
  states poorly, or vice versa.

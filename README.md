# Composio App Research — Take-Home Assignment

Research pipeline for the 100-app list: for each app, capture category, auth
method(s), self-serve vs. gated credentials, API surface, MCP existence,
buildability verdict, and evidence URLs — then find the cross-app patterns.

## Structure

```
data/
  apps.json          # the 100-app input list (id, app, category, hint)
  results.json        # per-app research output (seeded with 4 real, sourced entries)
  run_log.json         # per-app run status/timing once the agent has run
  verification.json    # sampled accuracy check output
scripts/
  research_agent.py   # the agent: Claude + web_search tool, researches each app
  verify_sample.py     # samples N results and checks each field against its cited sources
docs/
  index.html            # the deliverable — findings, patterns, process, verification
```

## How the research was actually done

**Apps 1–4 (Salesforce, HubSpot, Pipedrive, Attio)** were researched by hand,
one query per app, cross-referencing 3–9 sources each, with every field
backed by a real doc URL. This is the ground-truth seed and the format the
agent is instructed to reproduce.

**Apps 5–100** are designed to be researched by `research_agent.py`, which
does the identical job automatically: for each app, it calls Claude with the
web_search tool, asks it to find and structure the same fields, and requires
a source URL for every claim. This is the "do it with an agent, not by hand"
part of the assignment — the manual pass above proves the method works and
gives the agent a format to match; the script scales it to all 100.

**Where a human is still needed:** the agent's output isn't taken as ground
truth. `verify_sample.py` re-checks a random sample of the agent's results
against the cited URLs and reports field-level accuracy. Any app where the
verifier disagrees with the agent gets manually reviewed before being counted
in the final patterns — this is the actual verification loop the assignment
asks for, not a one-line "we checked it" claim.

## Running it

```bash
pip install anthropic
export ANTHROPIC_API_KEY=your_key_here

python scripts/research_agent.py     # researches all apps not already in results.json
python scripts/verify_sample.py --n 20   # samples 20 results and checks them against sources
```

Both scripts write incrementally (`results.json` / `verification.json` are
updated after every app), so a crash or rate limit mid-run loses at most one
app's progress, not the whole batch.

## Status

- [x] App list (100/100) captured in `data/apps.json`
- [x] Research agent script written and ready to run
- [x] Verification script written and ready to run
- [x] 4/100 apps researched and sourced by hand (seed/proof of concept)
- [ ] Remaining 96 apps — run `research_agent.py` with an API key to complete
- [ ] Pattern analysis — to be generated from the full `results.json` once complete
- [ ] Final deliverable page — structure built in `docs/index.html`, populates from `results.json`

## Notes on the deliverable

`docs/index.html` is a single self-contained page (deploy via GitHub Pages)
built to read `data/results.json` directly, so once the agent finishes the
remaining apps, the page updates without any redesign — regenerating the
data regenerates the report.

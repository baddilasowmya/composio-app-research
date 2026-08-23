"""
Quality gate for data/results.json.

Checks (per the assignment's own bar for a complete submission):
  - exactly 100 apps in apps.json, IDs 1-100, no duplicates
  - every result corresponds to a real app id
  - no result has an error field, or is missing auth_methods / credential_access /
    api_protocols / buildability_verdict / evidence
  - no duplicate result IDs
  - enum fields have valid values

Exits non-zero (and prints failures) if anything fails, so this can run in CI
or a pre-push check. Prints a coverage summary either way -- this is meant to
be run honestly at any point in the project, not just once it's "done."

Usage: python validate_results.py
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
APPS_FILE = DATA_DIR / "apps.json"
RESULTS_FILE = DATA_DIR / "results.json"

REQUIRED_FIELDS = ["auth_methods", "credential_access", "api_protocols", "buildability_verdict", "evidence"]
VALID_VERDICTS = {"ready", "partial", "blocked"}
VALID_ACCESS = {"self-serve", "gated", "unclear"}


def main():
    apps = json.loads(APPS_FILE.read_text())
    results = json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []

    errors = []
    checks_passed = []

    app_ids = [a["id"] for a in apps]
    if len(apps) != 100:
        errors.append(f"apps.json has {len(apps)} apps, expected 100")
    else:
        checks_passed.append("100/100 apps in apps.json")

    if sorted(app_ids) != list(range(1, 101)):
        errors.append("apps.json IDs are not exactly 1-100")
    else:
        checks_passed.append("app IDs are exactly 1-100")

    if len(app_ids) != len(set(app_ids)):
        errors.append("duplicate app IDs in apps.json")
    else:
        checks_passed.append("no duplicate app IDs")

    valid_app_ids = set(app_ids)
    result_ids = [r["id"] for r in results]
    if len(result_ids) != len(set(result_ids)):
        errors.append("duplicate result IDs in results.json")
    else:
        checks_passed.append("no duplicate result IDs")

    orphans = [r["id"] for r in results if r["id"] not in valid_app_ids]
    if orphans:
        errors.append(f"results with no matching app id: {orphans}")
    else:
        checks_passed.append("every result maps to a real app id")

    field_failures = []
    for r in results:
        if "error" in r:
            field_failures.append(f"id {r['id']} ({r.get('app')}): has an error field")
            continue
        for field in REQUIRED_FIELDS:
            if field not in r or r[field] in (None, "", []):
                field_failures.append(f"id {r['id']} ({r.get('app')}): missing/empty {field}")
        if r.get("buildability_verdict") not in VALID_VERDICTS:
            field_failures.append(f"id {r['id']} ({r.get('app')}): invalid buildability_verdict {r.get('buildability_verdict')!r}")
        if r.get("credential_access") not in VALID_ACCESS:
            field_failures.append(f"id {r['id']} ({r.get('app')}): invalid credential_access {r.get('credential_access')!r}")
        if not r.get("evidence") or not any((r.get("evidence") or {}).values()):
            field_failures.append(f"id {r['id']} ({r.get('app')}): no evidence for any field")

    if field_failures:
        errors.extend(field_failures)
    else:
        checks_passed.append(f"all {len(results)} researched records have complete required fields")

    coverage = len(results)
    print(f"Coverage: {coverage}/100 apps have a results.json record.\n")

    print("PASSED:")
    for c in checks_passed:
        print(f"  \u2713 {c}")

    if errors:
        print("\nFAILED:")
        for e in errors:
            print(f"  \u2717 {e}")
        print(f"\n{len(errors)} check(s) failed.")
        sys.exit(1)
    else:
        if coverage < 100:
            print(f"\nNo schema errors, but only {coverage}/100 apps are researched yet -- "
                  f"run research_agent.py to complete the remaining {100-coverage}.")
        else:
            print("\nAll 100/100 apps researched with complete, valid records. PASS.")


if __name__ == "__main__":
    main()

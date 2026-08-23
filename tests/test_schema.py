"""
Run with: python -m pytest tests/ -v
(or: python tests/test_schema.py  -- falls back to plain asserts if pytest isn't installed)
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
APPS_FILE = DATA_DIR / "apps.json"
RESULTS_FILE = DATA_DIR / "results.json"

VALID_VERDICTS = {"ready", "partial", "blocked"}
VALID_ACCESS = {"self-serve", "gated", "unclear"}
REQUIRED_RESULT_FIELDS = ["auth_methods", "credential_access", "api_protocols", "buildability_verdict", "evidence"]


def load_apps():
    return json.loads(APPS_FILE.read_text())


def load_results():
    return json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []


def test_apps_json_is_valid_json():
    load_apps()  # raises if invalid


def test_exactly_100_apps():
    apps = load_apps()
    assert len(apps) == 100, f"expected 100 apps, got {len(apps)}"


def test_app_ids_are_1_to_100_no_dupes():
    apps = load_apps()
    ids = sorted(a["id"] for a in apps)
    assert ids == list(range(1, 101)), "app IDs must be exactly 1-100 with no gaps or duplicates"


def test_results_json_is_valid_json():
    load_results()  # raises if invalid


def test_no_duplicate_result_ids():
    results = load_results()
    ids = [r["id"] for r in results]
    assert len(ids) == len(set(ids)), "duplicate IDs found in results.json"


def test_every_result_maps_to_a_real_app():
    apps = load_apps()
    valid_ids = {a["id"] for a in apps}
    results = load_results()
    orphans = [r["id"] for r in results if r["id"] not in valid_ids]
    assert not orphans, f"results with no matching app: {orphans}"


def test_no_result_has_an_error_field():
    results = load_results()
    errored = [r["id"] for r in results if "error" in r]
    assert not errored, f"results still containing an unresolved error field: {errored}"


def test_required_fields_present_and_nonempty():
    results = load_results()
    for r in results:
        for field in REQUIRED_RESULT_FIELDS:
            assert field in r and r[field] not in (None, "", []), \
                f"id {r['id']} ({r.get('app')}) missing/empty required field: {field}"


def test_buildability_verdict_is_valid_enum():
    results = load_results()
    for r in results:
        assert r.get("buildability_verdict") in VALID_VERDICTS, \
            f"id {r['id']} ({r.get('app')}) has invalid buildability_verdict: {r.get('buildability_verdict')!r}"


def test_credential_access_is_valid_enum():
    results = load_results()
    for r in results:
        assert r.get("credential_access") in VALID_ACCESS, \
            f"id {r['id']} ({r.get('app')}) has invalid credential_access: {r.get('credential_access')!r}"


def test_every_result_has_at_least_one_evidence_url():
    results = load_results()
    for r in results:
        evidence = r.get("evidence") or {}
        assert any(evidence.values()), f"id {r['id']} ({r.get('app')}) has no evidence for any field"


if __name__ == "__main__":
    # plain-assert runner so this works even without pytest installed
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failures += 1
    print(f"\n{len(tests)-failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

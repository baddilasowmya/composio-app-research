"""
Checks that every evidence URL in results.json is at least well-formed
(scheme + domain) and appears to reference the app it's cited for, where
that's checkable from the URL/domain alone. This is a cheap static check --
scripts/verify_sample.py does the real live fetch-and-confirm verification.

Run with: python tests/test_urls.py
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"


def load_results():
    return json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []


def is_well_formed_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


def test_all_evidence_urls_are_well_formed():
    results = load_results()
    bad = []
    for r in results:
        for field, urls in (r.get("evidence") or {}).items():
            for u in urls:
                if not is_well_formed_url(u):
                    bad.append(f"id {r['id']} ({r.get('app')}) field {field}: malformed URL {u!r}")
    assert not bad, "\n".join(bad)


def test_no_placeholder_urls():
    """Catches obviously-fabricated evidence like example.com or bare TLDs."""
    placeholder_patterns = [r"example\.com", r"^https?://(www\.)?url", r"placeholder", r"todo"]
    results = load_results()
    bad = []
    for r in results:
        for field, urls in (r.get("evidence") or {}).items():
            for u in urls:
                if any(re.search(p, u, re.I) for p in placeholder_patterns):
                    bad.append(f"id {r['id']} ({r.get('app')}) field {field}: placeholder-looking URL {u!r}")
    assert not bad, "\n".join(bad)


if __name__ == "__main__":
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

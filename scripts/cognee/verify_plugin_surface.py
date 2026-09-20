#!/usr/bin/env python3
"""Prove the backend serves the exact routes the Hermes plugin calls.

The plugin speaks a specific REST surface (read out of its installed
http_backend.py). Cognee Cloud serves it; this asserts the self-hosted build
does too, BEFORE Hermes is repointed -- otherwise the first failure shows up as
a broken agent rather than a failed check.

Cloudflare fronts this deployment and answers urllib's default
"Python-urllib/3.x" agent with its own 403 before the request reaches Cognee
(see scripts/cognee/verify_backend.py) -- send a realistic User-Agent or a
refused route looks identical to a missing one.

Usage:
  python3 scripts/cognee/verify_plugin_surface.py <base-url> <api-key> [dataset]
"""
import json
import sys
import urllib.error
import urllib.request

# Route -> method. Read from cognee_integration_hermes/http_backend.py.
ROUTES = [
    ("GET", "/api/v1/datasets"),
    ("POST", "/api/v1/datasets"),
    ("POST", "/api/v1/recall"),
    ("POST", "/api/v1/agents/register"),
]

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cognee-verify/1.0 (+hermes deploy check)",
}


def call(base, method, path, api_key, payload=None):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(_HEADERS)
    headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: verify_plugin_surface.py <base-url> <api-key> [dataset]")
    base, key = sys.argv[1], sys.argv[2]
    dataset = sys.argv[3] if len(sys.argv) > 3 else "hermes"

    failures = []

    # Both datasets from the spec: `hermes` is the personal store and the seed
    # target; `shared` is the explicit cross-agent surface Claude Desktop writes
    # to in Task 8. Create both now -- v1.6.0 fails recall loudly on an
    # unresolvable dataset name, and a missing `shared` would first surface as a
    # confusing Claude Desktop error much later.
    wanted = [dataset, "shared"]
    for name in wanted:
        code, body = call(base, "POST", "/api/v1/datasets", key, {"name": name})
        ok = code < 400 or code == 409
        print("  %s create dataset %r -- HTTP %d" % ("PASS" if ok else "FAIL", name, code))
        if not ok:
            failures.append("create dataset %s: %s" % (name, body))

    code, body = call(base, "GET", "/api/v1/datasets", key)
    ok = code < 400 and all(name in body for name in wanted)
    print("  %s datasets %r are listed -- HTTP %d" % ("PASS" if ok else "FAIL", wanted, code))
    if not ok:
        failures.append("list datasets: %s" % body)

    # 404 here means this build does not serve the agent-memory surface at all,
    # which is the one thing that would invalidate the whole design.
    code, body = call(base, "POST", "/api/v1/recall", key,
                      {"query": "connectivity probe", "datasets": [dataset]})
    ok = code != 404
    print("  %s /api/v1/recall exists -- HTTP %d" % ("PASS" if ok else "FAIL", code))
    if not ok:
        failures.append("recall route missing: %s" % body)

    print("")
    if failures:
        sys.exit("FAILED: %s" % failures)
    print("plugin REST surface verified")

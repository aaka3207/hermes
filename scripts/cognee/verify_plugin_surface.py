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

Every route in ROUTES is probed. A route that 404s (or 405s, served under a
different method) is a failure, so a backend missing the agent-register
surface cannot print "plugin REST surface verified".

Usage:
  python3 scripts/cognee/verify_plugin_surface.py <base-url> <api-key> [dataset]

`dataset` defaults to `shared` -- the seed target and the Hermes default.
"""
import json
import sys
import urllib.error
import urllib.request

# Method -> route. Read from cognee_integration_hermes/http_backend.py. Every
# entry here is probed below; the run fails if any is not served, and the
# `covered` assertion at the end fails if a route is ever added here without
# a probe to go with it.
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
    """Return (status, body). The body is returned in FULL -- truncate only
    when printing. A dataset listing is checked by parsing this body, and a
    backend with several datasets easily exceeds any fixed cut."""
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(_HEADERS)
    headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def dataset_names(body):
    """Dataset names out of a listing body, or None if it is not parseable.

    Cognee has returned both a bare list and an object wrapping one, so both
    shapes are accepted; anything else is reported as unparseable rather
    than guessed at.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        for key in ("datasets", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return None
    names = []
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: verify_plugin_surface.py <base-url> <api-key> [dataset]")
    base, key = sys.argv[1], sys.argv[2]
    # `shared` is the seed target and the Hermes default (since be03a149a);
    # `hermes` is the optional per-profile quarantine dataset.
    dataset = sys.argv[3] if len(sys.argv) > 3 else "shared"

    failures = []
    covered = set()

    # Both datasets from the spec. Create both now -- v1.6.0 fails recall
    # loudly on an unresolvable dataset name, and a missing one would first
    # surface as a confusing Claude Desktop error much later.
    wanted = [dataset]
    if "shared" not in wanted:
        wanted.append("shared")
    for name in wanted:
        code, body = call(base, "POST", "/api/v1/datasets", key, {"name": name})
        ok = code < 400 or code == 409
        print("  %s create dataset %r -- HTTP %d" % ("PASS" if ok else "FAIL", name, code))
        if not ok:
            failures.append("create dataset %s: %s" % (name, body[:300]))
    covered.add(("POST", "/api/v1/datasets"))

    # Parse the listing rather than substring-matching a truncated body: a
    # healthy backend with several datasets otherwise fails this spuriously.
    code, body = call(base, "GET", "/api/v1/datasets", key)
    listed = dataset_names(body) if code < 400 else None
    if code >= 400:
        ok = False
        detail = "HTTP %d: %s" % (code, body[:300])
    elif listed is None:
        ok = False
        detail = "listing body is not a recognisable dataset list: %s" % body[:300]
    else:
        missing = [name for name in wanted if name not in listed]
        ok = not missing
        detail = "missing %r (listed: %r)" % (missing, listed)
    print("  %s datasets %r are listed -- HTTP %d" % ("PASS" if ok else "FAIL", wanted, code))
    if not ok:
        failures.append("list datasets: %s" % detail)
    covered.add(("GET", "/api/v1/datasets"))

    # A 404/405 on either of these means this build does not serve the
    # agent-memory surface the plugin calls, which is the one thing that
    # would invalidate the whole design. Any other status means the route
    # exists -- a 4xx from validation is fine, the probe payloads are
    # deliberately minimal.
    probes = [
        ("POST", "/api/v1/recall",
         {"query": "connectivity probe", "datasets": [dataset]}),
        ("POST", "/api/v1/agents/register",
         {"name": "hermes-deploy-check"}),
    ]
    for method, path, payload in probes:
        code, body = call(base, method, path, key, payload)
        ok = code not in (404, 405)
        print("  %s %s %s exists -- HTTP %d"
              % ("PASS" if ok else "FAIL", method, path, code))
        if not ok:
            failures.append("%s %s missing: %s" % (method, path, body[:300]))
        covered.add((method, path))

    # ROUTES is this file's stated contract. Fail rather than quietly serve a
    # narrower check than the docstring promises.
    unprobed = [route for route in ROUTES if route not in covered]
    if unprobed:
        failures.append("routes declared in ROUTES but never probed: %r" % unprobed)

    print("")
    if failures:
        sys.exit("FAILED: %s" % failures)
    print("plugin REST surface verified (%d routes)" % len(ROUTES))

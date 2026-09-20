#!/usr/bin/env python3
"""Verify a freshly deployed Cognee backend before anything is written to it.

Checks the three things that are cheap now and expensive later:
  1. /health responds          -- the container actually came up
  2. an unauthenticated call is REFUSED -- ENABLE_BACKEND_ACCESS_CONTROL is
     really on. This is a public deployment; an open backend is an open memory
     store, and the failure is silent.
  3. the reported version matches the pin

Usage: python3 scripts/cognee/verify_backend.py https://cognee.aakashe.org
"""
import json
import sys
import urllib.error
import urllib.request

EXPECTED_VERSION_PREFIX = "1.6"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read().decode("utf-8", "replace")


def probe(base_url):
    base = base_url.rstrip("/")
    out = {"health": False, "auth_required": None, "version": None}

    try:
        code, body = _get(base + "/health")
        out["health"] = 200 <= code < 300
        try:
            out["version"] = (json.loads(body) or {}).get("version")
        except (ValueError, AttributeError, TypeError):
            pass
    except Exception as exc:
        out["health_error"] = str(exc)[:200]

    # An unauthenticated read of a protected route must be refused.
    try:
        code, _ = _get(base + "/api/v1/datasets")
        out["auth_required"] = False
        out["auth_detail"] = "unauthenticated GET /api/v1/datasets returned %d" % code
    except urllib.error.HTTPError as exc:
        out["auth_required"] = exc.code in (401, 403)
        out["auth_detail"] = "HTTP %d" % exc.code
    except Exception as exc:
        out["auth_required"] = False
        out["auth_detail"] = str(exc)[:200]

    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: verify_backend.py <base-url>")
    result = probe(sys.argv[1])
    print(json.dumps(result, indent=2))

    problems = []
    if not result["health"]:
        problems.append("backend /health did not respond")
    if result["auth_required"] is not True:
        problems.append(
            "backend answered an UNAUTHENTICATED request (%s) -- "
            "ENABLE_BACKEND_ACCESS_CONTROL is not in effect. Do not seed this "
            "deployment." % result.get("auth_detail"))
    if result["health"] and result.get("version") is None:
        problems.append("version not returned by /health -- cannot verify pin")
    version = result.get("version")
    if version and not str(version).startswith(EXPECTED_VERSION_PREFIX):
        problems.append("version %r does not match pin %s.x"
                        % (version, EXPECTED_VERSION_PREFIX))

    if problems:
        for p in problems:
            print("PROBLEM: %s" % p)
        sys.exit(1)
    print("backend verified")

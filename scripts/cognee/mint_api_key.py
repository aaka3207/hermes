#!/usr/bin/env python3
"""Log in to a self-hosted Cognee backend and print an API key.

The Hermes plugin cannot do this itself: http_backend._resolve_api_key only
mints for URLs it considers local (127.0.0.1/localhost) and raises a hard
configuration error for any other host. So the key is minted here, once, by
hand, and pasted into Coolify.

Mirrors the plugin's own flow exactly (POST /api/v1/auth/login ->
GET/POST /api/v1/auth/api-keys) so that what is minted is what it expects.

Cloudflare fronts this deployment and answers urllib's default
"Python-urllib/3.x" agent with a 403 of its own, before the request ever
reaches Cognee (see scripts/cognee/verify_backend.py). A realistic
User-Agent header avoids that edge-vs-origin confusion here too.

Usage:
  python3 scripts/cognee/mint_api_key.py https://cognee.aakashe.org <email> <password>
"""
import json
import sys
import urllib.parse
import urllib.request

KEY_NAME = "hermes-plugin"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cognee-verify/1.0 (+hermes deploy check)",
}


def _request(base, method, path, *, data=None, headers=None, cookies=None):
    url = base.rstrip("/") + path
    hdrs = dict(_HEADERS)
    hdrs.update(headers or {})
    if cookies:
        hdrs["Cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else None


def _looks_masked(key):
    """True if `key` is an elided display value rather than a usable secret.

    Masking conventions vary ("sk-...abcd", "****abcd", "abcd••••"), so this
    looks for the elision itself: a run of asterisks, bullets, or three or
    more consecutive dots.
    """
    return ("***" in key or "•" in key or "…" in key
            or "..." in key)


def mint(base, email, password):
    form = urllib.parse.urlencode({"username": email, "password": password}).encode()
    login = _request(
        base, "POST", "/api/v1/auth/login", data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = str((login or {}).get("access_token") or "")
    if not token:
        raise SystemExit("login returned no access token")

    cookies = {"auth_token": token}
    existing = _request(base, "GET", "/api/v1/auth/api-keys", cookies=cookies)
    if isinstance(existing, list):
        for item in existing:
            # Match on the name we asked for: taking existing[0] blindly
            # hands back whatever key happens to be first, which may belong
            # to something else entirely. A non-dict element is skipped
            # rather than raising on .get.
            if not isinstance(item, dict) or item.get("name") != KEY_NAME:
                continue
            key = str(item.get("key") or "")
            if not key:
                continue
            if _looks_masked(key):
                # Cognee has returned masked values on GET. Printing one as a
                # working key produces a credential that 401s with no clue why.
                raise SystemExit(
                    "the backend returned the existing %r key MASKED (%r), so "
                    "its real value cannot be read here. Delete that key in "
                    "the Cognee UI and re-run to mint a fresh one, or copy the "
                    "value you saved when it was created."
                    % (KEY_NAME, key))
            return token, key, "reused"

    created = _request(
        base, "POST", "/api/v1/auth/api-keys",
        data=json.dumps({"name": KEY_NAME}).encode(),
        headers={"Content-Type": "application/json"}, cookies=cookies)
    key = str((created or {}).get("key") or "")
    if not key:
        raise SystemExit("api-key creation returned no key")
    if _looks_masked(key):
        raise SystemExit(
            "api-key creation returned a MASKED value (%r), not a usable key "
            "-- read the real value from the Cognee UI instead of pasting "
            "this" % key)
    return token, key, "created"


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: mint_api_key.py <base-url> <email> <password>")
    bearer, api_key, how = mint(sys.argv[1], sys.argv[2], sys.argv[3])
    print("api_key (%s)  -> Hermes cognee.json `api_key`, seed <api-key>," % how)
    print("                 AND Coolify COGNEE_MCP_API_TOKEN:")
    print(api_key)
    print()
    print("The same API key goes in BOTH slots. Do NOT put the Bearer JWT in")
    print("COGNEE_MCP_API_TOKEN: it is sent as x-api-key")
    print("(COGNEE_API_AUTH_SCHEME=x-api-key) and, being a JWT, it EXPIRES --")
    print("Claude Desktop would work today and 401 silently weeks later.")
    print()
    print("bearer token -> short-lived session JWT, for ad-hoc curl only:")
    print(bearer)

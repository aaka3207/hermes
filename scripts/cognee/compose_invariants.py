#!/usr/bin/env python3
"""Assert the deployment invariants that review misses.

Most of this deployment's constraints are ABSENCES: no published host ports
(host :8000 is the Coolify dashboard), no floating image tags (an unpinned
image changes under a running deployment), no EMBEDDING_ENDPOINT (LiteLLM
appends /embeddings itself and a set endpoint yields .../embeddings/embeddings
-> 404). A human reading a diff does not notice a line that is not there.

Deliberately regex/line based rather than YAML-parsing: it must run with no
dependencies, in CI or on a bare host, and the checks are all lexical.
"""
import re

REQUIRED_SERVICES = ("cognee-backend", "cognee-mcp", "postgres")

PINNED = {
    "cognee/cognee": "1.6.0",
    "cognee/cognee-mcp": "main-bbec4a2",
    "pgvector/pgvector": "pg17",
}

FLOATING_TAGS = ("latest", "main", "dev-canary", "buildcache")


def check_compose(text):
    """Return a list of violation strings. Empty list means compliant."""
    violations = []
    lines = text.splitlines()

    for name in REQUIRED_SERVICES:
        if not re.search(r"^\s{2}%s:\s*$" % re.escape(name), text, re.M):
            violations.append(
                "missing or renamed service %r -- compose service names are the "
                "DNS aliases metamcp and Hermes resolve" % name)

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if re.match(r"^ports:\s*$", stripped) or re.match(r"^ports:\s*\[", stripped):
            violations.append(
                "line %d: `ports:` publishes a host port; host :8000 is the "
                "Coolify dashboard. Use `expose:` and route via Traefik." % i)

        m = re.match(r"^-?\s*image:\s*(\S+)$", stripped)
        if m:
            image = m.group(1)
            if ":" not in image:
                violations.append("line %d: image %r has no tag" % (i, image))
                continue
            repo, tag = image.rsplit(":", 1)
            if tag in FLOATING_TAGS:
                violations.append(
                    "line %d: image %r uses floating tag %r -- pin by version "
                    "or commit, or the image changes under a running deployment"
                    % (i, image, tag))
            elif repo in PINNED and tag != PINNED[repo]:
                violations.append(
                    "line %d: image %r is pinned to %r by the plan, found %r"
                    % (i, repo, PINNED[repo], tag))

        if "EMBEDDING_ENDPOINT" in stripped and not stripped.startswith("#"):
            violations.append(
                "line %d: EMBEDDING_ENDPOINT must not be set -- the openrouter/ "
                "model prefix must not be combined with an explicit endpoint, or "
                "LiteLLM requests .../embeddings/embeddings and 404s." % i)

        if re.search(r"CORS_ALLOWED_ORIGINS\s*[=:]\s*['\"]?\*", stripped):
            violations.append(
                "line %d: CORS_ALLOWED_ORIGINS is a wildcard on a public "
                "deployment" % i)

        if "super_secret" in stripped:
            violations.append(
                "line %d: FASTAPI_USERS_JWT_SECRET is left at the upstream "
                "default 'super_secret'" % i)

    return violations


if __name__ == "__main__":
    import sys
    path = (sys.argv[1] if len(sys.argv) > 1
            else "deploy/cognee-selfhost.compose.yaml")
    with open(path, encoding="utf-8") as handle:
        found = check_compose(handle.read())
    if found:
        for item in found:
            print("VIOLATION: %s" % item)
        sys.exit(1)
    print("%s: compliant" % path)

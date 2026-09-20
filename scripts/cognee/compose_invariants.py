#!/usr/bin/env python3
"""Assert the deployment invariants that review misses.

Most of this deployment's constraints are ABSENCES: no published host ports
(host :8000 is the Coolify dashboard), no floating image tags (an unpinned
image changes under a running deployment), no EMBEDDING_ENDPOINT (LiteLLM
appends /embeddings itself and a set endpoint yields .../embeddings/embeddings
-> 404). A human reading a diff does not notice a line that is not there.

A smaller set of constraints are PRESENCES: certain settings must exist with
an exact value (REQUIRED_SETTINGS). EMBEDDING_DIMENSIONS and EMBEDDING_MODEL
are the sharpest of these -- both are irreversible once the store has been
seeded, so a silent drift here is the most expensive failure this deployment
can have.

Deliberately regex/line based rather than YAML-parsing: it must run with no
dependencies, in CI or on a bare host, and the checks are all lexical.
"""
import re

REQUIRED_SERVICES = ("cognee-backend", "cognee-mcp", "cognee-ui",
                     "cognee-postgres")

PINNED = {
    "cognee/cognee": "1.6.0",
    "cognee/cognee-mcp": "main-bbec4a2",
    "cognee/cognee-ui": "1.6.0",
    "pgvector/pgvector": "pg17",
}

FLOATING_TAGS = ("latest", "main", "dev-canary", "buildcache")

# Env vars that must be present with exactly this value. Anything not listed
# here (secrets, per-deploy values like CORS origin) is intentionally out of
# scope -- this is for settings a future edit could silently drift.
REQUIRED_SETTINGS = {
    # pgvector is validated separately from DB_*; missing creds crash at boot.
    "VECTOR_DB_USERNAME": "cognee",
    # Coolify's own coolify-db publishes the alias `postgres` on the shared
    # network this stack joins, so `DB_HOST=postgres` resolves to COOLIFY'S
    # database and fails authentication for user `cognee`. Both host vars must
    # name the service explicitly.
    "DB_HOST": "cognee-postgres",
    "VECTOR_DB_HOST": "cognee-postgres",
    "EMBEDDING_DIMENSIONS": "1536",
    "EMBEDDING_MODEL": "openrouter/openai/text-embedding-3-small",
    "LLM_MODEL": "openrouter/deepseek/deepseek-v4-flash",
    "DB_PROVIDER": "postgres",
    "VECTOR_DB_PROVIDER": "pgvector",
    "GRAPH_DATABASE_PROVIDER": "kuzu",
    "ENABLE_BACKEND_ACCESS_CONTROL": "true",
    "ENV": "prod",
    "DEBUG": "false",
}

# Settings whose VALUE is a secret or a per-deploy reference, so only their
# presence can be checked. Each maps to what happens if the line is deleted.
REQUIRED_PRESENT = {
    "VECTOR_DB_PASSWORD":
        "pgvector credentials do not inherit from DB_PASSWORD; Cognee raises "
        "`OSError: Missing required pgvector credentials.` at startup",
    "FASTAPI_USERS_JWT_SECRET":
        "without it Cognee falls back to its own default signing secret, so "
        "anyone who knows the upstream default can mint valid tokens",
}

# Settings that must appear inside one specific service's environment.
SERVICE_REQUIRED_SETTINGS = {
    "cognee-mcp": {
        # The client defaults to Bearer auth for self-hosted URLs, and a
        # Bearer credential here is a JWT that EXPIRES -- Claude Desktop
        # works today and 401s weeks later. Cognee API keys do not expire.
        "COGNEE_API_AUTH_SCHEME": "x-api-key",
    },
}

# Changing these after the store has data corrupts it silently -- the
# violation message says so explicitly, because that message is what someone
# reads at 2am while deciding whether an edit is safe to ship.
IRREVERSIBLE_SETTINGS = ("EMBEDDING_DIMENSIONS", "EMBEDDING_MODEL")

_IRREVERSIBLE_NOTE = (
    " -- this value is effectively irreversible once the store is seeded: it "
    "is baked into the store at first use (the dimension "
    "becomes the pgvector column type), so changing it after data exists is "
    "not a restart: every existing embedding was written under the old "
    "value, and recovering means re-embedding the whole corpus and "
    "rebuilding the column, paying the OpenRouter cost again")


_SERVICE_RE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*$")
_ENV_RE = re.compile(r"^-\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _env_by_service(lines):
    """Map service name -> {ENV_NAME: value} for the first occurrence of each.

    Lexical, like everything else here: a line indented exactly two spaces
    and ending in a colon opens a service block; every `- NAME=value` item
    below it belongs to that service until the next such line.
    """
    by_service = {}
    current = None
    for line in lines:
        service = _SERVICE_RE.match(line.rstrip())
        if service and not line.lstrip().startswith("-"):
            current = service.group(1)
            by_service.setdefault(current, {})
            continue
        if current is None:
            continue
        m = _ENV_RE.match(line.strip())
        if m and m.group(1) not in by_service[current]:
            by_service[current][m.group(1)] = m.group(2)
    return by_service


def check_compose(text):
    """Return a list of violation strings. Empty list means compliant."""
    violations = []
    lines = text.splitlines()

    for name in REQUIRED_SERVICES:
        if not re.search(r"^\s{2}%s:\s*$" % re.escape(name), text, re.M):
            violations.append(
                "missing or renamed service %r -- compose service names are the "
                "DNS aliases metamcp and Hermes resolve, and `cognee-postgres` "
                "must not be shortened to `postgres`, which collides with "
                "coolify-db on the shared network" % name)

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

    setting_names = "|".join(re.escape(name) for name in REQUIRED_SETTINGS)
    setting_pattern = re.compile(r"^-\s*(%s)=(.*)$" % setting_names)
    found_settings = {}
    for line in lines:
        m = setting_pattern.match(line.strip())
        if m and m.group(1) not in found_settings:
            found_settings[m.group(1)] = m.group(2)

    for name, required in REQUIRED_SETTINGS.items():
        note = _IRREVERSIBLE_NOTE if name in IRREVERSIBLE_SETTINGS else ""
        if name not in found_settings:
            violations.append(
                "required setting %s is missing from the compose%s"
                % (name, note))
        elif found_settings[name] != required:
            violations.append(
                "required setting %s must be %r, found %r%s"
                % (name, required, found_settings[name], note))

    by_service = _env_by_service(lines)
    all_env = {}
    for env in by_service.values():
        for name, value in env.items():
            all_env.setdefault(name, value)

    for name, consequence in REQUIRED_PRESENT.items():
        if name not in all_env:
            violations.append(
                "required setting %s is missing from the compose -- %s"
                % (name, consequence))

    for service, settings in SERVICE_REQUIRED_SETTINGS.items():
        env = by_service.get(service)
        if env is None:
            continue  # the missing-service check above already covers this
        for name, required in settings.items():
            if name not in env:
                violations.append(
                    "required setting %s is missing from service %r -- "
                    "cognee-mcp then defaults to Bearer auth, and a Bearer "
                    "credential here is a JWT that expires" % (name, service))
            elif env[name] != required:
                violations.append(
                    "required setting %s on service %r must be %r, found %r"
                    % (name, service, required, env[name]))

    mcp_env = by_service.get("cognee-mcp")
    if mcp_env is not None:
        hosts = mcp_env.get("MCP_ALLOWED_HOSTS")
        if hosts is None:
            violations.append(
                "MCP_ALLOWED_HOSTS is missing from service 'cognee-mcp' -- the "
                "SSE transport's DNS-rebinding guard then allows loopback "
                "only, and every call from metamcp is rejected before it "
                "reaches a tool while the container reports healthy")
        else:
            entries = [entry.strip() for entry in hosts.split(",")]
            bad = [entry for entry in entries
                   if entry and not entry.endswith(":*")]
            if not [entry for entry in entries if entry]:
                violations.append("MCP_ALLOWED_HOSTS is empty")
            for entry in bad:
                violations.append(
                    "MCP_ALLOWED_HOSTS entry %r does not end in ':*' -- "
                    "without the port glob suffix the entry silently matches "
                    "nothing, so the host it names is still rejected" % entry)

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

# Self-Hosted Cognee on Coolify — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up self-hosted Cognee on Coolify, seeded from Mnemosyne, so the personal Hermes profile and Claude Desktop share one graph memory store — and so Cognee can be evaluated as a Mnemosyne replacement.

**Architecture:** A new Coolify **Service** (`cognee-backend` + `cognee-mcp` + `postgres`/pgvector) deployed from an inline compose definition, with the file kept in this repo as the source of truth. No image is built — all three are stock upstream. Hermes reaches the backend **directly from its in-process plugin** over the shared `coolify` Docker network. Claude Desktop reaches it through the **existing metamcp aggregator**, so `cognee-mcp` needs no public exposure. Only the backend gets a public FQDN.

**Tech Stack:** Docker Compose on Coolify, Cognee v1.6.0, pgvector/pg17, embedded Kuzu, OpenRouter (DeepSeek v4 Flash + `text-embedding-3-small`), Python 3 stdlib for all tooling.

**Spec:** `docs/superpowers/specs/2026-09-20-cognee-selfhost-design.md`

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **Image pins, by commit — never `main` or `latest`.** `cognee/cognee:1.6.0` and `cognee/cognee-mcp:main-bbec4a2` (release v1.6.0 = commit `bbec4a28b`). Fallback pair if pgvector misbehaves on first boot: `cognee/cognee:1.5.4` / `cognee/cognee-mcp:main-20e0bd8`.
- **No host port publishing.** Host `:8000` is the Coolify dashboard. Use `expose:`, never `ports:`.
- **Compose service names are load-bearing:** exactly `cognee-backend`, `cognee-mcp`, `postgres`. Coolify publishes these as DNS aliases on the shared `coolify` network.
- **`EMBEDDING_ENDPOINT` must not be set.** The `openrouter/` model prefix must not be combined with an explicit endpoint, or LiteLLM produces `.../embeddings/embeddings` and a 404.
- **`EMBEDDING_DIMENSIONS=1536`**, model `openrouter/openai/text-embedding-3-small`. This is irreversible once seeded.
- **`LLM_MODEL=openrouter/deepseek/deepseek-v4-flash`** (parity with the gateway's OpenRouter slot).
- **Set before first boot, or never:** `DEFAULT_USER_EMAIL`, `DEFAULT_USER_PASSWORD`. Changing them later does not update the created user.
- **`FASTAPI_USERS_JWT_SECRET` must be overridden** — it defaults to the literal string `super_secret`.
- **`ENABLE_BACKEND_ACCESS_CONTROL=true`**, `ENV=prod`, `DEBUG=false`, `CORS_ALLOWED_ORIGINS` explicit (never `*`).
- **One Cognee user.** Shared datasets would have to be addressed by UUID; the Hermes plugin passes dataset *names*.
- **Datasets:** `hermes` (personal, seed target), `shared` (cross-agent). `dinefile` stays on Cognee Cloud and is never touched.
- **Never write to the Mnemosyne SQLite store.** Open it `?mode=ro` only.
- **Production deploys and writes are gated.** Any step that deploys, restarts a running app, or edits `/opt/data/**` is marked **[GATED]** — prepare the exact command and hand it to the user. Do not execute it.
- **Real script files, not inline heredocs.** Tooling lives in committed `scripts/cognee/*.py`.
- **The repo is the source of truth for the compose.** Coolify holds a pasted copy. Edit `deploy/cognee-selfhost.compose.yaml`, re-run the invariant checker, commit, *then* paste. Never the other way round.
- **Tests are stdlib, run directly** (`python3 tests/test_x.py`), following `tests/test_cognee_cloud_smoke.py`: a module-level `failures = []`, a `check(name, ok, detail="")` helper, and `sys.exit("FAILED: %s" % failures)` at the end. No pytest.

## Where things live

**One repo.** There is no image build anywhere in this plan — all three images
are stock upstream — so git was only ever a place to keep a text file. The
compose is deployed as a **Coolify Service with an inline definition** (the same
shape as the existing `metamcp`, `paperclip` and `n8n` services, verified: they
store `docker_compose_raw` on the Service, with `service_type: null`).

Coolify holds the *running* copy; this repo holds the *source of truth*. Config
that exists only in Coolify's database has no diff, no review, and does not
survive a Coolify rebuild — which is the same class of problem
`docs/mnemosyne-operations.md` already flags about hand-maintained paths.

**Nothing in this repo's own `docker-compose.yaml` or `Dockerfile` changes.**
The container-wide `COGNEE_BASE_URL` / `COGNEE_API_KEY` stay pointed at Cognee
Cloud so the `dinefile` profile is untouched. The personal profile is switched
by editing runtime files on the Hermes volume (Task 7), not repo files.

## File Structure

```
hermes/
├── deploy/
│   ├── cognee-selfhost.compose.yaml   # source of truth; pasted into Coolify
│   └── cognee-selfhost.env.example    # every var, documented, no secrets
├── scripts/cognee/
│   ├── compose_invariants.py          # asserts the Global Constraints
│   ├── verify_backend.py              # /health, auth-required, version
│   ├── mint_api_key.py                # login -> reuse-or-create API key
│   ├── verify_plugin_surface.py       # the REST routes the plugin calls
│   ├── export_mnemosyne.py            # SQLite (ro) -> JSONL records
│   └── seed_cognee.py                 # JSONL -> POST /api/v1/remember
├── tests/
│   ├── test_compose_invariants.py
│   ├── test_export_mnemosyne.py
│   └── test_seed_cognee.py
└── docs/
    └── cognee-operations.md           # written in Task 9; supersedes the spec
```

`scripts/` is a new directory, per this repo's own RULES.md ("Place utility
scripts in `scripts/`, `tools/`, or `bin/`"). Tests go in the existing `tests/`
and follow `tests/test_cognee_cloud_smoke.py` exactly — stdlib only, run
directly, no pytest.

Each script has one responsibility and is independently runnable.
`export_mnemosyne.py` and `seed_cognee.py` are split deliberately: the export is
pure local computation that can be inspected before a single token is spent, and
the load is the irreversible half.

---

### Task 1: Compose file and invariant checks

**Files:**
- Create: `~/Documents/repos/hermes/deploy/cognee-selfhost.compose.yaml`
- Create: `~/Documents/repos/hermes/deploy/cognee-selfhost.env.example`
- Create: `~/Documents/repos/hermes/scripts/cognee/compose_invariants.py`
- Create: `~/Documents/repos/hermes/docs/cognee-operations.md`
- Test: `~/Documents/repos/hermes/tests/test_compose_invariants.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/cognee/compose_invariants.py` exposing `check_compose(text: str) -> list[str]`, returning a list of human-readable violation strings (empty list = compliant). Task 9 re-runs this as a regression guard.

The point of this task: the Global Constraints are mostly *absences* (no `ports:`, no `EMBEDDING_ENDPOINT`, no `latest` tag). Absences are exactly what human review misses, so they get a test.

- [ ] **Step 1: Create the directories and a feature branch**

```bash
cd ~/Documents/repos/hermes
git checkout feat/cognee-selfhost      # already exists; holds the spec and this plan
mkdir -p deploy scripts/cognee
printf 'deploy/*.env\n*.jsonl\n' >> .gitignore
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_compose_invariants.py`:

```python
#!/usr/bin/env python3
"""Tests for scripts/cognee/compose_invariants.py.

Pure stdlib, no docker needed:

    python3 tests/test_compose_invariants.py

The Global Constraints in the plan are mostly ABSENCES -- no published host
ports, no floating image tags, no EMBEDDING_ENDPOINT. Absences are what review
misses, so each one gets a case that proves the checker actually catches it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from compose_invariants import check_compose

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


GOOD = """
services:
  cognee-backend:
    image: cognee/cognee:1.6.0
    expose:
      - "8000"
    environment:
      - ENV=prod
      - DEBUG=false
      - ENABLE_BACKEND_ACCESS_CONTROL=true
      - FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}
      - CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org
      - EMBEDDING_DIMENSIONS=1536
      - EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
      - LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
  cognee-mcp:
    image: cognee/cognee-mcp:main-bbec4a2
    expose:
      - "8000"
  postgres:
    image: pgvector/pgvector:pg17
"""


def case(title, text, expect_substring):
    print("\n" + title)
    violations = check_compose(text)
    joined = " | ".join(violations)
    if expect_substring is None:
        check("no violations", violations == [], joined)
    else:
        check("flags %r" % expect_substring,
              any(expect_substring in v for v in violations), joined or "(none)")


case("1/8 compliant compose passes", GOOD, None)
case("2/8 published host port is rejected",
     GOOD.replace('    expose:\n      - "8000"',
                  '    ports:\n      - "8000:8000"', 1),
     "ports")
case("3/8 floating :latest tag is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "cognee/cognee:latest"),
     "latest")
case("4/8 floating :main tag is rejected",
     GOOD.replace("cognee/cognee-mcp:main-bbec4a2", "cognee/cognee-mcp:main"),
     "main")
case("5/8 EMBEDDING_ENDPOINT is rejected",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536",
                  "      - EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1\n"
                  "      - EMBEDDING_DIMENSIONS=1536"),
     "EMBEDDING_ENDPOINT")
case("6/8 wildcard CORS is rejected",
     GOOD.replace("CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org",
                  "CORS_ALLOWED_ORIGINS=*"),
     "CORS")
case("7/8 default JWT secret is rejected",
     GOOD.replace("FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}",
                  "FASTAPI_USERS_JWT_SECRET=super_secret"),
     "super_secret")
case("8/8 renamed service is rejected",
     GOOD.replace("  cognee-backend:", "  cognee:"),
     "cognee-backend")

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all compose invariant tests passed")
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `cd ~/Documents/repos/hermes && python3 tests/test_compose_invariants.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose_invariants'`

- [ ] **Step 4: Write the checker**

Create `scripts/cognee/compose_invariants.py`:

```python
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
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `python3 tests/test_compose_invariants.py`
Expected: PASS — `all compose invariant tests passed`

- [ ] **Step 6: Write the compose file**

Create `deploy/cognee-selfhost.compose.yaml`:

```yaml
# Self-hosted Cognee for the personal Hermes profile and Claude Desktop.
# Spec: hermes repo, docs/superpowers/specs/2026-09-20-cognee-selfhost-design.md
#
# Two clients share the STORE, not the transport:
#   Hermes         -> cognee-backend directly (in-process plugin, X-Api-Key)
#   Claude Desktop -> metamcp -> cognee-mcp -> cognee-backend
#
# NO `ports:` ANYWHERE. Host :8000 is the Coolify dashboard. Coolify routes the
# backend's published domain through Traefik; everything else stays internal.
services:
  cognee-backend:
    image: cognee/cognee:1.6.0
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
    expose:
      - "8000"
    environment:
      - ENV=prod
      - DEBUG=false
      - LOG_LEVEL=INFO

      # --- security: every one of these defaults to something unsafe ---
      - ENABLE_BACKEND_ACCESS_CONTROL=true
      - FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}
      - CORS_ALLOWED_ORIGINS=${COGNEE_CORS_ORIGINS}
      # Read ONLY on first boot. Changing these later does not update the
      # already-created superuser.
      - DEFAULT_USER_EMAIL=${COGNEE_DEFAULT_USER_EMAIL}
      - DEFAULT_USER_PASSWORD=${COGNEE_DEFAULT_USER_PASSWORD}

      # --- stores: postgres+pgvector relational/vector, embedded kuzu graph ---
      - DB_PROVIDER=postgres
      - DB_HOST=postgres
      - DB_PORT=5432
      - DB_NAME=cognee_db
      - DB_USERNAME=cognee
      - DB_PASSWORD=${COGNEE_DB_PASSWORD}
      - VECTOR_DB_PROVIDER=pgvector
      - VECTOR_DB_HOST=postgres
      - VECTOR_DB_PORT=5432
      - VECTOR_DB_NAME=cognee_db
      - GRAPH_DATABASE_PROVIDER=kuzu

      # --- models: both halves via OpenRouter, Cognee's `custom` provider ---
      - LLM_PROVIDER=custom
      - LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
      - LLM_ENDPOINT=https://openrouter.ai/api/v1
      - LLM_API_KEY=${OPENROUTER_API_KEY}
      - EMBEDDING_PROVIDER=custom
      - EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
      - EMBEDDING_API_KEY=${OPENROUTER_API_KEY}
      - EMBEDDING_DIMENSIONS=1536
      # EMBEDDING_ENDPOINT is deliberately absent -- see compose_invariants.py.
    volumes:
      - cognee_system:/cognee-storage/system
      - cognee_data:/cognee-storage/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  cognee-mcp:
    image: cognee/cognee-mcp:main-bbec4a2
    restart: always
    depends_on:
      - cognee-backend
    expose:
      - "8000"
    environment:
      # API mode: forward tool calls to the backend instead of running
      # pipelines locally, so the backend stays the single owner of the stores.
      - TRANSPORT_MODE=sse
      - API_URL=http://cognee-backend:8000
      - API_TOKEN=${COGNEE_MCP_API_TOKEN}

  postgres:
    image: pgvector/pgvector:pg17
    restart: always
    environment:
      - POSTGRES_USER=cognee
      - POSTGRES_DB=cognee_db
      - POSTGRES_PASSWORD=${COGNEE_DB_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cognee -d cognee_db"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  cognee_system:
  cognee_data:
  pg_data:
```

- [ ] **Step 7: Run the checker against the real file**

Run: `python3 scripts/cognee/compose_invariants.py deploy/cognee-selfhost.compose.yaml`
Expected: `deploy/cognee-selfhost.compose.yaml: compliant`

- [ ] **Step 8: Write `.env.example`**

```bash
# Copy into Coolify's environment variables UI. Never commit a filled-in .env.

# --- secrets ---
OPENROUTER_API_KEY=sk-or-v1-...
# Generate: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
COGNEE_JWT_SECRET=
COGNEE_DB_PASSWORD=

# --- first-boot-only: changing these later does NOT update the created user ---
COGNEE_DEFAULT_USER_EMAIL=ameerakashe@gmail.com
COGNEE_DEFAULT_USER_PASSWORD=

# --- set AFTER first boot, in Task 3 ---
# Bearer token cognee-mcp uses to reach the backend.
COGNEE_MCP_API_TOKEN=

# --- exposure ---
COGNEE_CORS_ORIGINS=https://cognee.aakashe.org
```

- [ ] **Step 9: Note what the operations doc will need**

The full runbook is written in Task 9 as `docs/cognee-operations.md`. It must contain: the two-client diagram from the spec, the deploy order (env vars BEFORE first deploy, because of the first-boot-only user), the pinned-image table with the fallback pair, and the one-line rollback (`memory.provider: cognee` -> `mnemosyne` in `/opt/data/config.yaml` on the Hermes volume).

- [ ] **Step 10: Commit**

```bash
cd ~/Documents/repos/hermes
git add deploy/ scripts/cognee/compose_invariants.py tests/test_compose_invariants.py .gitignore
git commit -m "feat(cognee): compose and invariant checks for self-hosted Cognee

Host :8000 is the Coolify dashboard, so nothing publishes a host port.
Images are pinned by version/commit because an unpinned tag changes under a
running deployment. EMBEDDING_ENDPOINT is deliberately absent: the openrouter/
prefix must not be combined with an explicit endpoint or LiteLLM 404s.

These constraints are all absences, which is what review misses, so
compose_invariants.py asserts each one."
```

---

### Task 2: Deploy and verify the backend **[GATED]**

**Files:**
- Create: `~/Documents/repos/hermes/scripts/cognee/verify_backend.py`

**Interfaces:**
- Consumes: the deployed `cognee-backend`.
- Produces: `verify_backend.py` exposing `probe(base_url: str) -> dict` with keys `health` (bool), `auth_required` (bool), `version` (str or None). Task 3 imports nothing from it; it is a standalone gate.

- [ ] **Step 1: Write the verifier**

Create `scripts/cognee/verify_backend.py`:

```python
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
        except ValueError:
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
    version = result.get("version")
    if version and not str(version).startswith(EXPECTED_VERSION_PREFIX):
        problems.append("version %r does not match pin %s.x"
                        % (version, EXPECTED_VERSION_PREFIX))

    if problems:
        for p in problems:
            print("PROBLEM: %s" % p)
        sys.exit(1)
    print("backend verified")
```

- [ ] **Step 2: Commit the verifier**

```bash
git add scripts/cognee/verify_backend.py
git commit -m "feat(cognee): pre-seed backend verification

Refusing an unauthenticated request is the load-bearing check. This backend is
publicly reachable, and an open one is an open memory store that fails silently."
```

- [ ] **Step 3: Hand the deploy to the user [GATED]**

Print the compose file for pasting, then **stop**:

```bash
cat deploy/cognee-selfhost.compose.yaml
```

Present the user with:

1. In Coolify, **Hermes** project → **+ New** → **Service** → **Docker Compose Empty**. Name it `cognee`. Paste the contents of `deploy/cognee-selfhost.compose.yaml` into the compose definition.
2. **Before the first deploy**, set every variable from `deploy/cognee-selfhost.env.example` in Coolify's environment UI. `COGNEE_DEFAULT_USER_EMAIL` and `COGNEE_DEFAULT_USER_PASSWORD` are read **only on first boot** and cannot be changed afterwards. Leave `COGNEE_MCP_API_TOKEN` empty for now — it is filled in Task 8.
3. Enable **Connect To Predefined Network** on this service. Verified 2026-09-20: Coolify Services do **not** join the shared `coolify` network by default (`paperclip`, its `db`, and `windmill-server` are each only on their own app network), so this must be set explicitly. Tasks 6 and 8 depend on it.
4. Set the domain for the **`cognee-backend`** service to `https://cognee.aakashe.org` in the Coolify UI. Prefer the UI over a `SERVICE_FQDN_*` variable — the magic-variable spelling for a hyphenated service name is not worth guessing.
5. Deploy.

**Whenever the compose changes later, edit `deploy/cognee-selfhost.compose.yaml` first, re-run the invariant checker, commit, and only then paste into Coolify.** The repo is the source of truth; Coolify holds a copy.

- [ ] **Step 4: Verify the deployment**

Run: `python3 scripts/cognee/verify_backend.py https://cognee.aakashe.org`
Expected: `backend verified`

**If `auth_required` is false, stop.** Do not continue to Task 3. Fix `ENABLE_BACKEND_ACCESS_CONTROL` and redeploy first.

- [ ] **Step 5: Confirm resource headroom [GATED]**

The host had ~7.4 GiB available with 2 GiB already swapped. Hand the user:

```bash
free -h && docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | grep -iE 'cognee|postgres'
```

Expected: the three new containers total well under 2 GB. If the host has started swapping harder, say so before seeding.

---

### Task 3: Mint the API key and verify the plugin's REST surface

**Files:**
- Create: `~/Documents/repos/hermes/scripts/cognee/mint_api_key.py`
- Create: `~/Documents/repos/hermes/scripts/cognee/verify_plugin_surface.py`

**Interfaces:**
- Consumes: a verified backend from Task 2.
- Produces: an `X-Api-Key` string used by Task 5 (`seed_cognee.py --api-key`) and Task 7 (`/opt/data/cognee.json`), and a Bearer token for `COGNEE_MCP_API_TOKEN` in Task 8. Also creates both datasets from spec §7: `hermes` and `shared`.

Why this task exists separately: the Hermes plugin will **not** mint its own key. `http_backend._resolve_api_key` only mints for URLs it considers local and raises a hard configuration error for any other host. The key must exist before Hermes is pointed at anything.

- [ ] **Step 1: Write the minter**

Create `scripts/cognee/mint_api_key.py`:

```python
#!/usr/bin/env python3
"""Log in to a self-hosted Cognee backend and print an API key.

The Hermes plugin cannot do this itself: http_backend._resolve_api_key only
mints for URLs it considers local (127.0.0.1/localhost) and raises a hard
configuration error for any other host. So the key is minted here, once, by
hand, and pasted into Coolify.

Mirrors the plugin's own flow exactly (POST /api/v1/auth/login ->
GET/POST /api/v1/auth/api-keys) so that what is minted is what it expects.

Usage:
  python3 scripts/cognee/mint_api_key.py https://cognee.aakashe.org <email> <password>
"""
import json
import sys
import urllib.parse
import urllib.request

KEY_NAME = "hermes-plugin"


def _request(base, method, path, *, data=None, headers=None, cookies=None):
    url = base.rstrip("/") + path
    hdrs = dict(headers or {})
    if cookies:
        hdrs["Cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else None


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
    if isinstance(existing, list) and existing:
        key = str(existing[0].get("key") or "")
        if key:
            return token, key, "reused"

    created = _request(
        base, "POST", "/api/v1/auth/api-keys",
        data=json.dumps({"name": KEY_NAME}).encode(),
        headers={"Content-Type": "application/json"}, cookies=cookies)
    key = str((created or {}).get("key") or "")
    if not key:
        raise SystemExit("api-key creation returned no key")
    return token, key, "created"


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: mint_api_key.py <base-url> <email> <password>")
    bearer, api_key, how = mint(sys.argv[1], sys.argv[2], sys.argv[3])
    print("api_key (%s)  -> Hermes cognee.json `api_key`, seed --api-key:" % how)
    print(api_key)
    print()
    print("bearer token -> Coolify COGNEE_MCP_API_TOKEN (see Task 8 caveat):")
    print(bearer)
```

- [ ] **Step 2: Write the surface verifier**

Create `scripts/cognee/verify_plugin_surface.py`:

```python
#!/usr/bin/env python3
"""Prove the backend serves the exact routes the Hermes plugin calls.

The plugin speaks a specific REST surface (read out of its installed
http_backend.py). Cognee Cloud serves it; this asserts the self-hosted build
does too, BEFORE Hermes is repointed -- otherwise the first failure shows up as
a broken agent rather than a failed check.

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


def call(base, method, path, api_key, payload=None):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"})
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
```

- [ ] **Step 3: Mint the key [GATED]**

Hand the user:

```bash
cd ~/Documents/repos/hermes
python3 scripts/cognee/mint_api_key.py https://cognee.aakashe.org \
  "<COGNEE_DEFAULT_USER_EMAIL>" "<COGNEE_DEFAULT_USER_PASSWORD>"
```

- [ ] **Step 4: Verify the surface**

Run: `python3 scripts/cognee/verify_plugin_surface.py https://cognee.aakashe.org "<api-key>" hermes`
Expected: `plugin REST surface verified`, with both `hermes` and `shared` created and listed.

**If `/api/v1/recall` returns 404, stop and reassess.** That route existing is the assumption the entire design rests on.

- [ ] **Step 5: Commit**

```bash
git add scripts/cognee/mint_api_key.py scripts/cognee/verify_plugin_surface.py
git commit -m "feat(cognee): API key minting and REST surface verification

The plugin refuses to mint a key for a non-local URL, so the key must exist
before Hermes is repointed. Verifying /api/v1/recall before seeding turns the
design's core assumption into a check instead of a broken agent."
```

---

### Task 4: Export the Mnemosyne store

**Files:**
- Create: `~/Documents/repos/hermes/scripts/cognee/export_mnemosyne.py`
- Test: `~/Documents/repos/hermes/tests/test_export_mnemosyne.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `export_mnemosyne.py` exposing `export(db_path: str) -> list[dict]`, each dict having keys `source` (`"memories"` | `"episodic_memory"` | `"working_memory"`), `text` (str), `created_at` (str or None). Task 5's `seed_cognee.py` consumes exactly this shape from JSONL.

Split from the load deliberately: this half is pure local computation and costs nothing, so its output can be eyeballed before a single token is spent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_export_mnemosyne.py`:

```python
#!/usr/bin/env python3
"""Tests for scripts/cognee/export_mnemosyne.py.

Builds a synthetic SQLite store with the real Mnemosyne table shapes, so no
access to the live 95 MB store is needed and the tests are safe to run anywhere.

    python3 tests/test_export_mnemosyne.py

The read-only guarantee is the load-bearing case: the live store is the
rollback path for the whole evaluation, so the exporter must be incapable of
writing to it.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from export_mnemosyne import export

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def build_store(path):
    con = sqlite3.connect(path)
    con.execute("create table memories (id integer primary key, content text, created_at text)")
    con.execute("create table episodic_memory (id integer primary key, content text, created_at text)")
    con.execute("create table working_memory (id integer primary key, content text, created_at text)")
    con.execute("insert into memories (content, created_at) values (?,?)",
                ("Ameer prefers plain markdown deliverables.", "2026-01-01T00:00:00"))
    con.execute("insert into episodic_memory (content, created_at) values (?,?)",
                ("Deployed the RTK rewrite on 2026-09-19.", "2026-09-19T10:00:00"))
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                ("  ", "2026-09-19T11:00:00"))          # blank -> must be skipped
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                (None, "2026-09-19T12:00:00"))           # null -> must be skipped
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                ("Uses Coolify on a single home server.", "2026-09-19T13:00:00"))
    con.commit()
    con.close()


tmp = tempfile.mkdtemp(prefix="mnemo-export-")
db = os.path.join(tmp, "mnemosyne.db")
build_store(db)

print("\n1/5 exports every non-empty row")
rows = export(db)
check("3 rows exported", len(rows) == 3, "got %d" % len(rows))

print("\n2/5 blank and null content are skipped")
texts = [r["text"] for r in rows]
check("no blank text", all(t and t.strip() for t in texts), repr(texts))

print("\n3/5 every row is tagged with its source table")
sources = sorted({r["source"] for r in rows})
check("sources tagged", sources == ["episodic_memory", "memories", "working_memory"],
      repr(sources))

print("\n4/5 timestamps are preserved")
check("created_at preserved",
      any(r["created_at"] == "2026-01-01T00:00:00" for r in rows),
      repr([r["created_at"] for r in rows]))

print("\n5/5 the source database is opened READ-ONLY")
before = os.path.getmtime(db)
export(db)
after = os.path.getmtime(db)
check("mtime unchanged", before == after, "%s -> %s" % (before, after))
uri_used = getattr(export, "LAST_URI", "")
check("opened via mode=ro URI", "mode=ro" in uri_used, repr(uri_used))

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all mnemosyne export tests passed")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 tests/test_export_mnemosyne.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'export_mnemosyne'`

- [ ] **Step 3: Write the exporter**

Create `scripts/cognee/export_mnemosyne.py`:

```python
#!/usr/bin/env python3
"""Export the Mnemosyne SQLite store to JSONL for seeding into Cognee.

READ-ONLY, always. The live store is the rollback path for the entire
evaluation: if Cognee does not work out, flipping memory.provider back is only
safe because this file was never touched. The connection is opened through a
`file:...?mode=ro` URI so a write is refused by SQLite rather than merely
avoided by us.

Verified row counts on the live store (2026-09-20): memories 3, episodic_memory
252, working_memory 1212 -- about 312 KB of prose in total.

Usage:
  python3 scripts/cognee/export_mnemosyne.py /path/to/mnemosyne.db > seed.jsonl
"""
import json
import sqlite3
import sys

TABLES = ("memories", "episodic_memory", "working_memory")


def export(db_path):
    """Return a list of {source, text, created_at} dicts, newest table last."""
    uri = "file:%s?mode=ro" % db_path
    export.LAST_URI = uri
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    records = []
    try:
        for table in TABLES:
            try:
                cursor = con.execute(
                    "select content, created_at from %s order by rowid" % table)
            except sqlite3.OperationalError:
                continue  # table absent in this schema version; not fatal
            for row in cursor:
                text = (row["content"] or "").strip()
                if not text:
                    continue
                records.append({
                    "source": table,
                    "text": text,
                    "created_at": row["created_at"],
                })
    finally:
        con.close()
    return records


export.LAST_URI = ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: export_mnemosyne.py <path-to-mnemosyne.db>")
    for record in export(sys.argv[1]):
        print(json.dumps(record, ensure_ascii=False))
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 tests/test_export_mnemosyne.py`
Expected: PASS — `all mnemosyne export tests passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/cognee/export_mnemosyne.py tests/test_export_mnemosyne.py
git commit -m "feat(cognee): read-only Mnemosyne exporter

Opened through a file:...?mode=ro URI so SQLite refuses a write rather than us
merely not attempting one. The live store is the rollback path for the whole
evaluation; it must come out of this unchanged."
```

---

### Task 5: Seed Cognee **[GATED run]**

**Files:**
- Create: `~/Documents/repos/hermes/scripts/cognee/seed_cognee.py`
- Test: `~/Documents/repos/hermes/tests/test_seed_cognee.py`

**Interfaces:**
- Consumes: JSONL records from Task 4 (`{source, text, created_at}`); an API key from Task 3.
- Produces: nothing consumed by later tasks. This is the irreversible half — it spends OpenRouter tokens and writes the `hermes` dataset.

- [ ] **Step 1: Write the failing test**

Create `tests/test_seed_cognee.py`:

```python
#!/usr/bin/env python3
"""Tests for scripts/cognee/seed_cognee.py, against a real local HTTP server.

    python3 tests/test_seed_cognee.py

A fake server is used rather than mocks so the multipart body, headers and
retry behaviour are exercised as the real backend would see them. Nothing here
touches the network or the live deployment.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from seed_cognee import seed

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


received = []
fail_times = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        received.append({
            "path": self.path,
            "api_key": self.headers.get("X-Api-Key"),
            "body": body.decode("utf-8", "replace"),
        })
        if fail_times["n"] > 0:
            fail_times["n"] -= 1
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')


server = HTTPServer(("127.0.0.1", 0), Handler)
base = "http://127.0.0.1:%d" % server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

RECORDS = [
    {"source": "memories", "text": "Prefers plain markdown.", "created_at": "2026-01-01T00:00:00"},
    {"source": "working_memory", "text": "Uses Coolify.", "created_at": "2026-09-19T13:00:00"},
]

print("\n1/5 every record is posted to /api/v1/remember")
del received[:]
sent = seed(base, "test-key", "hermes", RECORDS, batch_size=1)
check("2 requests made", len(received) == 2, "got %d" % len(received))
check("reports 2 sent", sent == 2, "got %r" % sent)
check("hits /api/v1/remember",
      all(r["path"].startswith("/api/v1/remember") for r in received),
      repr([r["path"] for r in received]))

print("\n2/5 the API key is sent on every request")
check("X-Api-Key present",
      all(r["api_key"] == "test-key" for r in received),
      repr([r["api_key"] for r in received]))

print("\n3/5 the dataset name is in the payload")
check("datasetName present",
      all("hermes" in r["body"] for r in received),
      (received[0]["body"][:160] if received else "(none)"))

print("\n4/5 provenance survives into the posted text")
check("source table and timestamp included",
      any("working_memory" in r["body"] and "2026-09-19" in r["body"] for r in received),
      (received[-1]["body"][:220] if received else "(none)"))

print("\n5/5 a transient 503 is retried rather than dropping a memory")
del received[:]
fail_times["n"] = 1
sent = seed(base, "test-key", "hermes", RECORDS[:1], batch_size=1, retries=2, backoff=0.01)
check("retried after 503", len(received) == 2, "got %d attempts" % len(received))
check("still reports 1 sent", sent == 1, "got %r" % sent)

server.shutdown()
print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee seed tests passed")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 tests/test_seed_cognee.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed_cognee'`

- [ ] **Step 3: Write the seeder**

Create `scripts/cognee/seed_cognee.py`:

```python
#!/usr/bin/env python3
"""Load exported Mnemosyne records into a Cognee dataset.

This is the irreversible half: it spends OpenRouter tokens and writes the
`hermes` dataset. Run scripts/cognee/export_mnemosyne.py first and read the JSONL.

Provenance (source table + timestamp) is prepended to each record's text rather
than dropped. Cognee extracts entities from the text it is given, so a bare
sentence loses when the memory was formed and which Mnemosyne bank it came
from -- which is exactly the context that makes an old memory judgeable later.

A transient failure is retried: a dropped record is a silently missing memory,
and there is no natural place to notice it.

Usage:
  python3 scripts/cognee/export_mnemosyne.py mnemosyne.db > seed.jsonl
  python3 scripts/cognee/seed_cognee.py https://cognee.aakashe.org <api-key> hermes seed.jsonl
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BOUNDARY = "----cognee-seed-%s" % uuid.uuid4().hex


def _multipart(fields, filename, payload):
    parts = []
    for name, value in fields.items():
        parts.append("--%s\r\n" % BOUNDARY)
        parts.append('Content-Disposition: form-data; name="%s"\r\n\r\n' % name)
        parts.append("%s\r\n" % value)
    parts.append("--%s\r\n" % BOUNDARY)
    parts.append(
        'Content-Disposition: form-data; name="data"; filename="%s"\r\n' % filename)
    parts.append("Content-Type: text/plain\r\n\r\n")
    body = "".join(parts).encode("utf-8") + payload.encode("utf-8")
    body += ("\r\n--%s--\r\n" % BOUNDARY).encode("utf-8")
    return body


def render(record):
    """Text for one memory, carrying its provenance."""
    stamp = record.get("created_at") or "unknown time"
    return "[%s | recorded %s]\n%s" % (record.get("source", "mnemosyne"), stamp,
                                       record["text"])


def post_one(base, api_key, dataset, text, index, retries=3, backoff=1.0):
    body = _multipart({"datasetName": dataset}, "mnemosyne-%05d.txt" % index, text)
    url = base.rstrip("/") + "/api/v1/remember"
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "multipart/form-data; boundary=%s" % BOUNDARY,
            })
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return 200 <= resp.getcode() < 300
        except Exception as exc:
            code = getattr(exc, "code", None)
            # 4xx other than 429 will not improve on a retry.
            if code is not None and 400 <= code < 500 and code != 429:
                print("  record %d: HTTP %s -- not retrying" % (index, code),
                      file=sys.stderr)
                return False
            if attempt > retries:
                print("  record %d: giving up after %d attempts (%s)"
                      % (index, attempt, str(exc)[:120]), file=sys.stderr)
                return False
            time.sleep(backoff * attempt)


def seed(base, api_key, dataset, records, batch_size=1, retries=3, backoff=1.0):
    """Post each record. Returns the number successfully sent."""
    sent = 0
    for index, record in enumerate(records):
        if post_one(base, api_key, dataset, render(record), index,
                    retries=retries, backoff=backoff):
            sent += 1
        if (index + 1) % 50 == 0:
            print("  ... %d/%d" % (index + 1, len(records)), file=sys.stderr)
    return sent


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit("usage: seed_cognee.py <base-url> <api-key> <dataset> <jsonl>")
    base_url, key, ds, path = sys.argv[1:5]
    with open(path, encoding="utf-8") as handle:
        items = [json.loads(line) for line in handle if line.strip()]
    print("seeding %d records into dataset %r" % (len(items), ds), file=sys.stderr)
    count = seed(base_url, key, ds, items)
    print("seeded %d/%d records" % (count, len(items)))
    if count != len(items):
        sys.exit(1)
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 tests/test_seed_cognee.py`
Expected: PASS — `all cognee seed tests passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/cognee/seed_cognee.py tests/test_seed_cognee.py
git commit -m "feat(cognee): seeder with provenance and retry

Provenance is prepended to each record because Cognee extracts from the text it
is given; a bare sentence loses when the memory was formed. Transient failures
retry, since a dropped record is a silently missing memory with no natural
place to notice it."
```

- [ ] **Step 6: Export from the live store [GATED]**

Run it from inside the Hermes container, which already has Python 3 and has the
Mnemosyne volume mounted at `/opt/data/mnemosyne/data`. Hand the user:

```bash
docker cp scripts/cognee/export_mnemosyne.py \
  hermes-tgg4k0sc8wgocck08cc4s4cg-153650733060:/tmp/export_mnemosyne.py
docker exec hermes-tgg4k0sc8wgocck08cc4s4cg-153650733060 \
  /opt/hermes/.venv/bin/python /tmp/export_mnemosyne.py \
  /opt/data/mnemosyne/data/mnemosyne.db > ~/cognee-seed.jsonl
wc -l ~/cognee-seed.jsonl   # expect ~1467
```

- [ ] **Step 7: Eyeball the export before spending anything**

Run: `head -3 ~/cognee-seed.jsonl && wc -l ~/cognee-seed.jsonl`
Expected: ~1467 lines; each line has `source`, `text`, `created_at`. If the count is far off 1467, stop and reconcile against the verified counts (memories 3, episodic 252, working 1212).

- [ ] **Step 8: Run the seed [GATED]**

```bash
python3 scripts/cognee/seed_cognee.py https://cognee.aakashe.org "<api-key>" hermes ~/cognee-seed.jsonl
```

Expected: `seeded 1467/1467 records`. This is where OpenRouter tokens are spent (~$0.0016 embeddings plus a few cents of extraction).

- [ ] **Step 9: Confirm the graph actually built [GATED]**

```bash
python3 scripts/cognee/verify_plugin_surface.py https://cognee.aakashe.org "<api-key>" hermes
```

Then a real recall for something only the seed could know:

```bash
curl -s -X POST https://cognee.aakashe.org/api/v1/recall \
  -H "X-Api-Key: <api-key>" -H "Content-Type: application/json" \
  -d '{"query":"What does Ameer prefer for deliverables?","datasets":["hermes"]}'
```

Expected: a result referencing the markdown preference. An empty result means the records landed but `cognify` has not finished — wait and retry before concluding anything.

---

### Task 6: Put Hermes on the shared network

**Files:** none in either repo — this is Coolify configuration.

**Interfaces:**
- Consumes: the deployed backend from Task 2.
- Produces: `http://cognee-backend:8000` resolvable from inside the Hermes container, which Task 7's `cognee.json` depends on.

- [ ] **Step 1: Enable the predefined network on Hermes [GATED]**

In Coolify, open the **hermes** application → Configuration → Network, enable **Connect To Predefined Network**, save, and redeploy.

**This restarts the Hermes gateway** — the only change this plan makes to the running Hermes application. The `cognee` application already had this enabled in Task 2.

- [ ] **Step 2: Verify Hermes is on the coolify network**

```bash
docker inspect hermes-tgg4k0sc8wgocck08cc4s4cg-153650733060 \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Expected: output now includes `coolify` alongside `tgg4k0sc8wgocck08cc4s4cg`.

- [ ] **Step 3: Verify Hermes can reach the backend by service name**

```bash
docker exec hermes-tgg4k0sc8wgocck08cc4s4cg-153650733060 \
  curl -s -o /dev/null -w 'code=%{http_code} total=%{time_total}s\n' \
  --max-time 10 http://cognee-backend:8000/health
```

Expected: `code=200`, and `total` an order of magnitude below the ~0.070–0.110 s measured through the public FQDN. If DNS does not resolve, confirm the compose service is named exactly `cognee-backend`.

**Fallback:** if the predefined network proves awkward, use `https://cognee.aakashe.org` in Task 7 instead and record the tunnel dependency in the operations doc.

---

### Task 7: Switch the personal profile to Cognee **[GATED]**

**Files:**
- Modify (on the Hermes volume, not in the repo): `/opt/data/cognee.json`
- Modify (on the Hermes volume, not in the repo): `/opt/data/config.yaml:137`

**Interfaces:**
- Consumes: the API key from Task 3, the seeded `hermes` dataset from Task 5, network reachability from Task 6.
- Produces: the running evaluation.

The single most important detail: **`api_key` must be set explicitly in this file.** `load_config` does a blanket `config.update(file_config)`, so file keys override env — but `dinefile/cognee.json` *omits* `api_key` and inherits the Cloud key from the container-wide `COGNEE_API_KEY`. If the personal file also omits it, it inherits the **Cloud** key and every call 401s against the self-hosted backend.

Note the file uses *config* names (`service_url`, `api_key`), not env names (`COGNEE_BASE_URL`, `COGNEE_API_KEY`).

- [ ] **Step 1: Back up both files [GATED]**

```bash
C=hermes-tgg4k0sc8wgocck08cc4s4cg-153650733060
docker exec $C cp /opt/data/cognee.json /opt/data/cognee.json.bak-preselfhost
docker exec $C cp /opt/data/config.yaml /opt/data/config.yaml.bak-preselfhost
```

- [ ] **Step 2: Point the personal profile at the self-hosted backend [GATED]**

The file currently reads:

```json
{
  "auto_route": true,
  "dataset": "hermes",
  "improve_on_end": true,
  "service_url": "https://tenant-acc9068f-ff2e-4085-8303-874696f1dcd7.aws.cognee.ai"
}
```

Replace it with:

```json
{
  "auto_route": true,
  "dataset": "hermes",
  "improve_on_end": true,
  "service_url": "http://cognee-backend:8000",
  "api_key": "<api-key from Task 3>"
}
```

Leave `/opt/data/profiles/dinefile/cognee.json` **untouched** — it keeps inheriting the Cloud URL and Cloud key.

- [ ] **Step 3: Verify dinefile is still on Cloud [GATED]**

```bash
docker exec $C cat /opt/data/profiles/dinefile/cognee.json
```

Expected: still the `tenant-acc9068f-....aws.cognee.ai` URL, still no `api_key`. If this changed, revert it before continuing.

- [ ] **Step 4: Flip the provider [GATED]**

In `/opt/data/config.yaml`, line 137, change `provider: mnemosyne` to `provider: cognee` under the `memory:` block (lines 131–137). Change nothing else in that block.

- [ ] **Step 5: Restart the gateway and confirm the provider is live [GATED]**

```bash
docker restart $C
sleep 30
docker logs --tail 80 $C 2>&1 | grep -iE "cognee|memory provider|mnemosyne"
```

Expected: Cognee initialises; no `COGNEE_API_KEY is required for a remote cognee server` error (that message means Step 2's `api_key` is missing or wrong).

- [ ] **Step 6: End-to-end check through the agent**

Ask the agent something only the seed could answer, then confirm a new memory is written and recalled in a later turn.

- [ ] **Step 7: Record the rollback in the operations doc**

```bash
# Rollback: one line, plus a restart. The Mnemosyne store was never written to.
docker exec $C sed -i 's/^  provider: cognee$/  provider: mnemosyne/' /opt/data/config.yaml
docker restart $C
```

---

### Task 8: Put cognee-mcp behind metamcp

**Files:** none in either repo — Coolify env var plus metamcp UI configuration.

**Interfaces:**
- Consumes: the Bearer token from Task 3, the running `cognee-mcp` from Task 2.
- Produces: `https://metamcp.aakashe.org/metamcp/cognee/mcp` for Claude Desktop.

This path is entirely independent of Task 7. If it breaks, **Hermes is unaffected** — it shares none of this machinery.

- [ ] **Step 1: Set the MCP token and redeploy [GATED]**

Set `COGNEE_MCP_API_TOKEN` in the `cognee` application's Coolify environment to the Bearer token from Task 3, then redeploy that application.

- [ ] **Step 2: Verify cognee-mcp is reachable from metamcp**

```bash
docker exec app-vkokwgggswokogg484oww0gc \
  curl -s -o /dev/null -w 'code=%{http_code}\n' --max-time 10 \
  http://cognee-mcp:8000/sse
```

Expected: a non-connection-error response. If DNS fails, confirm both applications have **Connect To Predefined Network** enabled and the service is named exactly `cognee-mcp`.

- [ ] **Step 3: Register cognee in metamcp [GATED]**

Through the metamcp **UI** (not raw SQL), mirroring the existing `Monarch` registration, which already uses an internal URL:

| Object | Value |
|---|---|
| MCP server | name `cognee`, type **SSE**, url `http://cognee-mcp:8000/sse` |
| Namespace | `cognee`, with the `cognee` server mapped into it |
| Endpoint | `cognee`, **`enable_api_key_auth` = true** |

- [ ] **Step 4: Point Claude Desktop at it [GATED]**

Add `https://metamcp.aakashe.org/metamcp/cognee/mcp` with a metamcp API key, exactly as the existing `mnemosyne-endpoint` is configured.

- [ ] **Step 5: Verify cross-agent sharing actually works**

The point of the whole exercise. From Claude Desktop, write a memory into the `shared` dataset. Then, from Hermes, recall it. Then the reverse.

If recall fails in one direction only, check that both clients are using the **same dataset name** — v1.6.0 now fails loudly on an unresolvable dataset name, so the error should say so rather than return quietly wrong results.

- [ ] **Step 6: Note the token-expiry finding**

Record in the operations doc whether `COGNEE_MCP_API_TOKEN` is a long-lived key or a short-lived JWT. **This is the plan's one open question.** If it expires, Claude Desktop starts returning 401 with no other symptom, and Hermes keeps working — which makes it easy to misdiagnose.

---

### Task 9: Operations doc and evaluation record

**Files:**
- Create: `~/Documents/repos/hermes/docs/cognee-operations.md`

**Interfaces:**
- Consumes: findings from every prior task.
- Produces: the document that supersedes the spec.

- [ ] **Step 1: Re-run every test as a regression gate**

```bash
cd ~/Documents/repos/hermes
python3 scripts/cognee/compose_invariants.py deploy/cognee-selfhost.compose.yaml
python3 tests/test_compose_invariants.py
python3 tests/test_export_mnemosyne.py
python3 tests/test_seed_cognee.py
# the pre-existing suite must still pass -- nothing here should touch it
python3 tests/test_cognee_cloud_smoke.py
```

Expected: all pass.

- [ ] **Step 2: Write `docs/cognee-operations.md`**

Mirror `docs/mnemosyne-operations.md`. It must contain:

- The two-client diagram, stating plainly that Hermes does **not** go through MCP or metamcp.
- A component inventory with the real Coolify app UUID, container names, and both FQDNs.
- The pinned image table and the fallback pair.
- The exact rollback command from Task 7 Step 7.
- Which files are per-profile and which are container-wide, and the `api_key`-inheritance trap that makes `dinefile` work and would break the personal profile if the key were omitted.
- The token-expiry finding from Task 8 Step 6.
- Measured results against the §10 criteria: recall relevance, added per-turn latency, cost per active day, operational noise, cross-agent sharing.

- [ ] **Step 3: Record the evaluation verdict**

Against the spec's §10 criteria, state plainly whether Cognee should replace Mnemosyne, and why. An evaluation with no recorded verdict becomes the new status quo by default — and every day it runs, the Mnemosyne store goes staler and rollback costs more.

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/repos/hermes
git add docs/cognee-operations.md
git commit -m "docs(cognee): operations handoff for self-hosted Cognee

Supersedes the design spec. Records the measured evaluation results, the
rollback command, and the api_key inheritance trap between the two profiles."
```

---

## Notes for the executor

- **Task order matters in two places.** `DEFAULT_USER_EMAIL`/`DEFAULT_USER_PASSWORD` are read only on first boot (Task 2 before Task 3), and the seed must land before the provider flip (Task 5 before Task 7) or the first real session meets an empty store.
- **Tasks 7 and 8 are independent.** Either can be done first, and a failure in one does not implicate the other.
- **If `/api/v1/recall` 404s in Task 3, stop.** That route existing on the self-hosted build is the assumption the whole design rests on. Everything after it is wasted work if it is false.

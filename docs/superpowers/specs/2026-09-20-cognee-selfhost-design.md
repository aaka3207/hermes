# Self-Hosted Cognee on Coolify — Design

**Status:** design approved, not yet implemented
**Date:** 2026-09-20
**Goal:** evaluate self-hosted Cognee as a replacement for Mnemosyne on the personal
(default) Hermes profile, with memory shared between Hermes and Claude Desktop.

Everything marked *verified* below was checked against the live estate on 2026-09-20.
Everything else is a decision or an assumption, and is labelled as such.

---

## 1. Why this exists

Mnemosyne is the current memory provider for the personal profile. Cognee Cloud
already backs the `dinefile` profile and works well there. The open question is
whether self-hosted Cognee is a good enough replacement for Mnemosyne on the
personal profile — better recall, at an acceptable cost and latency.

This is an **evaluation**, so the design optimises for reversibility. The Mnemosyne
SQLite store is never written to, never moved, and never migrated. Rolling back is
one line in one file.

### Non-goals

- Migrating the `dinefile` profile off Cognee Cloud. It keeps its own config and
  stays on Cloud throughout.
- Retiring Mnemosyne. That is a decision to make *after* the evaluation, not part
  of this work.
- Multi-user Cognee. One user, two clients — see §5.

---

## 2. Architecture

A new Coolify application, `cognee`, deployed from a new repository
(`aaka3207/cognee-selfhost`) holding a single `docker-compose.yaml`. This mirrors
the existing `mnemosyne-mcp` application rather than inventing a new pattern.

```
  Claude Desktop                          Hermes gateway (personal profile)
        |                                                |
   HTTPS + metamcp API key                               |
        |                                                |
        v                                                |
  metamcp (already deployed, public)                      |
        |                                                |
   http://cognee-mcp:8000/sse      ── shared `coolify` network ──
        |                                                |
        v                                     HTTP + X-Api-Key
   cognee-mcp ── HTTP (API mode) ──> cognee-backend <────┘
                                           |
                                  +--------+--------+
                                  |                 |
                           postgres+pgvector    kuzu (embedded)
                           relational+vector    graph
```

Only `cognee-backend` is published to the internet (for its API and web UI).
`cognee-mcp` and `postgres` stay internal.

### Two independent client paths

This is the most important thing to keep straight, because the two clients reach
the same store by completely different routes:

| Client | Transport | Path | Auth |
|---|---|---|---|
| **Hermes** | in-process plugin, plain HTTP | **directly to `cognee-backend`** | `X-Api-Key` |
| **Claude Desktop** | MCP | metamcp → `cognee-mcp` → `cognee-backend` | metamcp API key |

**Hermes does not use MCP and does not go through metamcp.** The
`cognee_integration_hermes` plugin is already baked into the Hermes image and
speaks REST to `/api/v1/remember`, `/api/v1/recall`, `/api/v1/improve`,
`/api/v1/forget` directly. Routing Hermes through MCP would add two hops and a
second auth scheme for no benefit, and would lose the plugin's session-layer
recall, dataset switching and code-graph lanes, none of which the MCP tool surface
exposes.

They share the **store**, not the transport. That is the whole point: one backend
owns the databases, and both clients are thin.

| Service | Image | Exposure |
|---|---|---|
| `cognee-backend` | `cognee/cognee:1.6.0` | `cognee.aakashe.org` (API + UI) |
| `cognee-mcp` | `cognee/cognee-mcp:main-bbec4a2` | **internal only** — fronted by metamcp |
| `postgres` | `pgvector/pgvector:pg17` | internal only |

**Compose service names are load-bearing.** Coolify publishes each service's
compose name as a DNS alias on the shared `coolify` network, which is how metamcp
already reaches `http://monarch-mcp:9000/sse` (verified). The services must
therefore be named exactly `cognee-backend` and `cognee-mcp`.

`cognee-mcp` runs in **API mode** (`API_URL=http://cognee-backend:8000`). In this
mode it forwards tool calls to the backend instead of running pipelines locally,
so the backend remains the single owner of the databases.

### Why this is better than the Mnemosyne topology

The Mnemosyne deployment shares one SQLite *file* across three containers by
bind-mounting the same host directory at three different container paths. Per
`docs/mnemosyne-operations.md`, if one side's path changes "the store silently
forks into two files and cross-agent recall stops working without any error."

Cognee has no shared file. Every client speaks HTTP to one backend that owns the
stores. That entire class of silent-fork failure does not exist here.

### Deployment constraints (verified)

- **Host port 8000 is already bound by the Coolify dashboard.** The compose file in
  Cognee's own Coolify guide publishes `ports: - "8000:8000"`, which would collide.
  This deployment uses `expose` plus Coolify's `SERVICE_FQDN_*` variable (backend only) and lets
  Traefik route by hostname. No host port publishing at all.
- **Hermes is not on the shared `coolify` Docker network.** It sits alone on its app
  network `tgg4k0sc8wgocck08cc4s4cg`. See §6 for how the Hermes hop is resolved.

---

## 3. Stores

Postgres with pgvector for the relational and vector layers; embedded **kuzu** for
the graph. Postgres runs inside this application's own compose file rather than as
a Coolify-managed database resource, so the whole thing can be deleted in one action.

**Why pgvector rather than the all-embedded sqlite + lancedb option.** The seed
(§8) costs an LLM ingestion pass. Moving from embedded stores to Postgres later
means paying that pass again, because it is a re-ingest and not a migration. The
destination store should therefore be chosen before the seed is paid for, not after.

**Why not Neo4j.** It would add 1–2 GB to a host already 2 GB into swap, and the
Community edition allows only one database per server, which forces
`ENABLE_BACKEND_ACCESS_CONTROL=false` and destroys per-dataset isolation.

### Resource budget (verified host state)

Host: 8 cores, 15 GiB RAM, ~7.4 GiB available, 2 GiB swap already in use, 364 GB
disk free. Expected footprint for the three new containers is roughly 1.5–2 GB RSS.
This fits, but the host is not spacious — it is the reason local inference is off
the table and the reason Neo4j is rejected.

---

## 3a. Version pinning

Verified 2026-09-20 by mapping GitHub release tags to their commits and matching
those commits to published image tags. This is **not** inferred from build dates.

| Release | Commit | `cognee` image | `cognee-mcp` image |
|---|---|---|---|
| **v1.6.0** (2026-09-18) | `bbec4a28b` | `cognee/cognee:1.6.0` | `cognee/cognee-mcp:main-bbec4a2` |
| v1.5.4 (2026-09-04) | `20e0bd887` | `cognee/cognee:1.5.4` | `cognee/cognee-mcp:main-20e0bd8` |

`cognee/cognee-mcp` publishes **no release semver tags** — its only semver-looking
tags are dev builds (`1.5.3.dev1`, `1.5.0.dev5`). Releases ship as `main-<sha>`, so
the MCP image must be pinned by commit. **Never use `main` or `latest`:** an
unpinned image can change under a running deployment.

**Decision: v1.6.0 / `main-bbec4a2`.** Three of its changes land directly on this
design:

- *"Fail search/recall on unresolvable dataset names — instead of proceeding
  silently with incorrect assumptions."* This is the same class of failure that
  bit the Mnemosyne deployment (silent fork, no error), and it matters here
  because the Hermes plugin passes dataset **names**, not UUIDs (§5).
- *"Scope agent prompt recall bodies to a dataset — reducing cross-dataset
  leakage."* Datasets are this design's isolation boundary (§7).
- *"Record and check embedding model once per dataset."* A guard against the one
  irreversible decision in this design (§4).

The counter-argument — a two-day-old release refactoring Postgres adapters — is
addressed in §11.

---

## 4. Models

Both halves go through OpenRouter. Cognee supports this natively via its `custom`
provider; no shim is required.

```bash
LLM_PROVIDER=custom
LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
LLM_ENDPOINT=https://openrouter.ai/api/v1
LLM_API_KEY=${OPENROUTER_API_KEY}

EMBEDDING_PROVIDER=custom
EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
EMBEDDING_API_KEY=${OPENROUTER_API_KEY}
EMBEDDING_DIMENSIONS=1536
# EMBEDDING_ENDPOINT is deliberately NOT set. Cognee's docs are explicit that the
# `openrouter/` model prefix must not be combined with an explicit endpoint: the
# `custom` provider passes EMBEDDING_ENDPOINT straight to LiteLLM as api_base and
# LiteLLM appends /embeddings itself, producing .../embeddings/embeddings and a 404.
```

### Extraction model

`deepseek/deepseek-v4-flash` — the model the gateway itself already uses for the
OpenRouter slot (verified in `/opt/data/config.yaml`). Using the same model keeps
extraction quality comparable to Mnemosyne's. Note there is **no `:free` DeepSeek
variant on OpenRouter**; this is a paid model at $0.036/M prompt tokens, which is
negligible at this volume but is not zero.

### Embedding model — measured, not assumed

Dimensions are not exposed by OpenRouter's `/api/v1/embeddings/models` listing, and
`EMBEDDING_DIMENSIONS` is the one value the store locks in permanently. Measured
directly on 2026-09-20 with the live key:

| Model | Dims | Latency | Price | Result |
|---|---|---|---|---|
| `openai/text-embedding-3-small` | 1536 | 0.42s | $0.02/M | works |
| `nvidia/nemotron-3-embed-1b:free` | — | — | free | **HTTP 404** |
| `baai/bge-m3` | 1024 | 0.35s | $0.01/M | works |
| `qwen/qwen3-embedding-8b` | 4096 | 1.15s | $0.01/M | works |

The free Nemotron model returned *"0 endpoints out of 1 requested are available
matching your guardrail restrictions and data policy"* — free endpoints require
opting into OpenRouter's training/data-publication policy. That opt-in is declined
here: this is a personal memory store, and the data policy is precisely what would
be relaxed.

**Decision: `openai/text-embedding-3-small` at 1536 dimensions.** It matches
Mnemosyne's existing embedding dimension, so the A/B comparison is not confounded
by a different embedding space. Cost is not a factor — the entire seed is ~80K
tokens, about **$0.0016**. Embeddings are not where Cognee spends money; the
`cognify` extraction passes are.

`baai/bge-m3` is the strongest alternative (half the price, 1024 dims, smaller
index) and is the model to revisit if the evaluation succeeds and the store is ever
rebuilt from scratch.

---

## 5. Identity, authentication and exposure

**One Cognee user, two clients.** This is a deliberate constraint, not a shortcut.
With backend access control enabled, Cognee's docs state that *shared* datasets must
be referenced by **UUID rather than by name**. The Hermes plugin passes dataset
*names*. Keeping a single owning user keeps names valid everywhere and avoids that
breakage entirely.

### Hardening

Every one of these defaults to something unsafe, and all of them matter now that the
service is publicly reachable.

| Variable | Setting | Why |
|---|---|---|
| `ENABLE_BACKEND_ACCESS_CONTROL` | `true` (default) | requires auth on the API |
| `FASTAPI_USERS_JWT_SECRET` | long random value | **defaults to the literal string `super_secret`** |
| `CORS_ALLOWED_ORIGINS` | explicit origins | defaults to `*` |
| `DEFAULT_USER_EMAIL` / `DEFAULT_USER_PASSWORD` | set before first boot | changing them later does **not** update the already-created user |
| `ENV` | `prod` | any other value enables FastAPI debug |
| `DEBUG` | `false` | |

### Authentication paths

- **Hermes → backend:** `X-Api-Key`. Minted once by hand against the self-hosted
  server (`POST /api/v1/auth/login` → `POST /api/v1/auth/api-keys`) and stored in
  Coolify. The plugin will not mint this itself: `http_backend._resolve_api_key`
  only mints for URLs it considers local, and raises a hard configuration error for
  any other host.
- **cognee-mcp → backend:** `Authorization: Bearer`. A *different* scheme; per the
  docs, self-hosted backends use Bearer while Cloud tenants are auto-detected and use
  `X-Api-Key`. **Open item:** if that token is a JWT it will expire, and the refresh
  story needs to be established at deploy time rather than assumed. See §11.
- **Claude Desktop → metamcp:** metamcp API key (`enable_api_key_auth = true`),
  exactly as for Mnemosyne today. See §9a.

Note that a token expiry on the second bullet degrades **Claude Desktop only**.
Hermes keeps working, because its path shares none of that machinery.

---

## 6. Reaching the backend from Hermes

Hermes reaches the backend **directly from the plugin** (§2, "Two independent
client paths"). It never goes through MCP or metamcp, so this hop is on the path of
every single recall and remember — it is the latency-sensitive one.

`cognee.aakashe.org` resolves to Cloudflare (verified: `2606:4700:...`), so traffic
would exit to the Cloudflare edge and return through the tunnel. Measured from
inside the Hermes container against the existing `mnemosyne-mcp.aakashe.org`:
**~70–110 ms** round trip.

Acceptable for a browser. Wrong for Hermes, which would pay it on every turn and
would gain a hard dependency on the Cloudflare tunnel for its memory to work at all.

**Decision:** enable Coolify's *Connect To Predefined Network* on both the `hermes`
and `cognee` applications so both join the shared `coolify` bridge network. Hermes
then reaches `http://cognee-backend:8000` over the local bridge.

This is **the established pattern in this estate, not a novel move** (verified
2026-09-20): metamcp already sits on `coolify` and reaches
`http://monarch-mcp:9000/sse` by compose-service alias. The same mechanism carries
metamcp → `cognee-mcp` (§9a) and Hermes → `cognee-backend`.

**Cost:** toggling the predefined network on the `hermes` app restarts it. This is
the only change this design makes to the running Hermes application.

**Fallback if that proves awkward:** point Hermes at `https://cognee.aakashe.org`.
Works on day one with no networking changes, at ~100 ms per call — but note the
tunnel dependency above before accepting it as permanent.

---

## 7. The memory model — datasets

Datasets are the isolation boundary, and are the Cognee analogue of Mnemosyne's two
banks — without the shared-file fragility.

**REVISED 2026-09-20, after reading the shipped plugin.** The original split below
does not work, and the reason is not visible from Cognee's documentation.

`cognee_integration_hermes/provider.py` (`_recall_scope_params`) always passes
`[self._dataset]` — a single-element list. **Hermes reads exactly one dataset at a
time.** Cognee itself searches every readable dataset when `datasets` is omitted, and
`cognee-mcp`'s `recall` exposes `datasets` as optional, so Claude Code *can* span
both; Hermes cannot. The plugin never sends `node_set`, `labels` or
`external_metadata` either, so Hermes writes cannot be tagged natively.

Therefore the portable layer must BE the dataset Hermes defaults to:

| Dataset | Read by | Written by | Purpose |
|---|---|---|---|
| `shared` | Hermes (default), Claude Code | both | **the portable layer**: durable facts, preferences, relationships. The seed target. |
| `hermes` | Hermes only when switched into | Hermes | optional quarantine for noisy per-project work, via `cognee_switch_dataset` |
| `dinefile` | — | — | **untouched; stays on Cognee Cloud** |

Session and permanent memory are separated *inside* a dataset, so one shared dataset
does not mean session chatter contaminates durable facts. The plugin's own default
(`agent_sessions`, one dataset for everything) relies on exactly this.

### 7a. Attribution

One shared dataset means Hermes will recall things Claude Code wrote and vice versa,
so every memory must say who formed it — otherwise an agent treats another agent's
context as its own.

Native tagging is unavailable for runtime writes (the plugin sends no `node_set`), so
attribution is **a convention in the memory text**, enforced by prompt instruction:

    [hermes] <memory>
    [claude-code] <memory>
    [mnemosyne] <memory>      # seeded history

Where we control the payload — the seeder — we ALSO set `node_set`, which the backend
accepts on `POST /api/v1/remember` and which recall can filter through `nodeName`.
That gives a machine-queryable origin for seeded rows without depending on the text
convention.

Cognee supports per-dataset ACLs (`read` / `write` / `delete` / `share`), so this can
grow into finer-grained sharing later. Not needed for the evaluation.

---

## 8. Seeding from Mnemosyne

The Mnemosyne store is 95 MB on disk but almost all of that is embeddings and FTS
indexes. The actual prose is small (verified 2026-09-20):

| Table | Rows | Text |
|---|---|---|
| `memories` | 3 | 8 KB |
| `episodic_memory` | 252 | 72 KB |
| `working_memory` | 1212 | 232 KB |
| **Total** | **1467** | **~312 KB** |

At ~80K tokens the seed costs roughly $0.0016 in embeddings plus a few cents of
`cognify` extraction. Cost is not a reason to start empty.

**Approach:** a committed script in the `cognee-selfhost` repo (a real file, not an
inline heredoc) that reads the SQLite store read-only, renders each row to text with
its timestamp and author, and `POST`s to `/api/v1/remember` into the `hermes`
dataset. The Mnemosyne database is opened `?mode=ro` and is never modified.

Seeding happens **before** the provider is flipped, so the first real session sees a
populated store.

---

## 9. Wiring Hermes — and the rollback

Three changes, all reversible.

1. **`/opt/data/cognee.json`** — set `service_url` to the self-hosted backend and
   **add `api_key` explicitly**.

   This is mandatory and is the easiest thing to get wrong. `load_config` does a
   blanket `config.update(file_config)`, so file keys override environment. But
   `dinefile/cognee.json` omits `api_key` and therefore *inherits* the Cloud key from
   the container-wide `COGNEE_API_KEY`. If the personal profile's file also omits it,
   it inherits the Cloud key and every call 401s against the self-hosted server.

   Note the file uses *config* names (`service_url`, `api_key`), not the environment
   names (`COGNEE_BASE_URL`, `COGNEE_API_KEY`).

2. **`/opt/data/config.yaml`** — `memory.provider: mnemosyne` → `cognee`.

3. **Claude Desktop** — register cognee in metamcp (see §9a). Hermes is unaffected
   by this step; it does not go through metamcp.

The container-wide `COGNEE_BASE_URL` / `COGNEE_API_KEY` environment variables stay
pointed at Cognee Cloud, so `dinefile` is entirely unaffected.

## 9a. Registering cognee in metamcp

metamcp is already deployed, already public, and already the front door Claude
Desktop uses. Fronting `cognee-mcp` with it means the MCP endpoint needs no public
FQDN of its own — strictly less exposed than Mnemosyne, which is published at
`mnemosyne-mcp.aakashe.org`.

Mirror the existing `Monarch` registration, which already uses an internal URL
(verified). Three rows, created through the metamcp **UI**, not raw SQL:

| Table | Value |
|---|---|
| `mcp_servers` | name `cognee`, type `SSE`, url `http://cognee-mcp:8000/sse` |
| `namespaces` | `cognee`, with the `cognee` server mapped into it |
| `endpoints` | `cognee`, **`enable_api_key_auth = true`** |

Claude Desktop then points at `https://metamcp.aakashe.org/metamcp/cognee/mcp`.

`cognee-mcp` runs with `TRANSPORT_MODE=sse` to match how metamcp reaches Monarch
and Mnemosyne today.

---

### Rollback

Revert change 2. Mnemosyne's SQLite store was never touched and resumes immediately.

**What rollback does not recover:** memories formed during the evaluation live in
Cognee, not Mnemosyne, so Mnemosyne's store is stale from the switch-over date. The
longer the evaluation runs, the more there is to lose by reverting.

---

## 10. What "good enough to keep" means

The evaluation needs an endpoint, or it will run forever. Judge against Mnemosyne on:

- **Recall relevance.** Does the injected memory block contain facts that are
  actually relevant to the turn? This is the question that matters most, and the
  seed exists to make it a fair comparison.
- **Per-turn latency added.** Mnemosyne's baseline is near-zero. Measure the
  recall path specifically.
- **Cost per active day.** Cognee runs LLM entity extraction on every remember, and
  `improve_on_end: true` adds a pass at session end. Mnemosyne was effectively free.
- **Operational noise.** Failed cognify runs, stuck pipelines, 401s after token
  expiry, restarts required.
- **Cross-agent sharing actually working.** Write from Claude Desktop, recall from
  Hermes, and the reverse.

---

## 11. Risks and open items

| Item | Assessment |
|---|---|
| `cognee-mcp` Bearer token expiry | **Open.** Must be established at deploy time. If it is a short-lived JWT, a refresh mechanism is needed or Claude Desktop will silently start 401ing. |
| Cost and latency per turn | This is the finding being sought, not a defect. Measure it rather than predict it. |
| Mnemosyne store goes stale | Inherent to the evaluation. Bounded by keeping the evaluation short. |
| v1.6.0 adapter refactor | v1.6.0 lists *"Database & adapter refactors may require review (including changes to Postgres/hybrid adapters). Self-hosted deployments should review their configuration."* That warning targets deployments **upgrading existing data** through the refactor. This is a greenfield deploy with no data to migrate, so it lands on the new adapters directly. Accepted — but if pgvector misbehaves on first boot, v1.5.4 / `main-20e0bd8` is the fallback pair. |
| GLiNER dropped from the 1.6.0 image | v1.6.0 removes GLiNER from the default Docker image. Not used here — extraction goes to OpenRouter. Noted so it is not mistaken for a regression. |
| Host headroom | ~7.4 GiB available with 2 GiB already swapped. Watch memory after deploy; this is the main reason for the store choices in §3. |
| Hermes app UUID change | If the `hermes` Coolify app is ever recreated, its network name changes. Using the shared `coolify` network (§6) avoids inheriting the fragility already documented for Mnemosyne's bind-mount path. |

---

## 12. Graduation

When this ships and the evaluation concludes, this document is superseded by
`docs/cognee-operations.md`, written to mirror `docs/mnemosyne-operations.md` —
component inventory, UUIDs, endpoints, and the failure modes actually encountered.

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
  Claude Desktop                      Hermes gateway (personal profile)
        |                                          |
   MCP (SSE/HTTP)                        HTTP + X-Api-Key
        |                                          |
        v                                          |
   cognee-mcp  ── HTTP (API mode) ──> cognee-backend <───────┘
                                            |
                                   +--------+--------+
                                   |                 |
                            postgres+pgvector    kuzu (embedded)
                            relational+vector    graph
```

| Service | Image | Exposure |
|---|---|---|
| `cognee-backend` | `cognee/cognee:1.5.4` | `cognee.aakashe.org` (API + UI) |
| `cognee-mcp` | `cognee/cognee-mcp:main-20e0bd8` | `cognee-mcp.aakashe.org/sse` |
| `postgres` | `pgvector/pgvector:pg17` | internal only |

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
  This deployment uses `expose` plus Coolify's `SERVICE_FQDN_*` variables and lets
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

---

## 6. Reaching the backend from Hermes

Both FQDNs resolve to Cloudflare (verified: `2606:4700:...`), so traffic exits to the
Cloudflare edge and returns through the tunnel. Measured from inside the Hermes
container against the existing `mnemosyne-mcp.aakashe.org`: **~70–110 ms** round trip.

That is fine for a browser and for Claude Desktop. It is the wrong choice for Hermes,
which would pay it on every recall and every remember, and which would gain a hard
dependency on the Cloudflare tunnel for its memory to work at all.

**Decision:** enable Coolify's *Connect To Predefined Network* on both the `hermes`
and `cognee` applications so both join the shared `coolify` bridge network. Hermes
then reaches `http://cognee-backend:8000` directly. The FQDNs remain for Claude
Desktop and the web UI.

**Cost:** toggling the predefined network on the `hermes` app restarts it.

**Fallback if that proves awkward:** point Hermes at `https://cognee.aakashe.org`.
It works on day one with no networking changes, at ~100 ms per call.

---

## 7. The memory model — datasets

Datasets are the isolation boundary, and are the Cognee analogue of Mnemosyne's two
banks — without the shared-file fragility.

| Dataset | Written by | Purpose |
|---|---|---|
| `hermes` | Hermes personal profile | personal memory; the seed target |
| `shared` | Hermes + Claude Desktop | the explicit cross-agent surface |
| `dinefile` | — | **untouched; stays on Cognee Cloud** |

Cognee supports per-dataset ACLs (`read` / `write` / `delete` / `share`) and recall
accepts a list of datasets, so this can grow into finer-grained sharing later. It is
not needed for the evaluation.

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

3. **Claude Desktop** — add the `cognee-mcp` endpoint, either directly or through the
   existing metamcp aggregator it already uses for Mnemosyne.

The container-wide `COGNEE_BASE_URL` / `COGNEE_API_KEY` environment variables stay
pointed at Cognee Cloud, so `dinefile` is entirely unaffected.

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
| Backend image version | Pinned to `1.5.4` (stable, 2026-09-04) rather than `1.6.0` (2026-09-18, two days old) or `main`. The Hermes plugin package pins `cognee==1.5.x`. |
| **MCP image has no matching semver tag** | Verified 2026-09-20: `cognee/cognee-mcp` publishes no `1.5.4`. Its only semver tags are dev builds (`1.5.3.dev1`, `1.5.0.dev5`); releases ship as `main-<sha>`. Pinned to `main-20e0bd8`, built 2026-09-04 — the same day as backend 1.5.4, so probably the matching commit, but this is **inferred from build date, not confirmed**. Verify the MCP tools work against the 1.5.4 backend at deploy time; if they do not, walk the `main-<sha>` tags. Do not use `main` or `latest` — an unpinned MCP image can change under a running deployment. |
| Host headroom | ~7.4 GiB available with 2 GiB already swapped. Watch memory after deploy; this is the main reason for the store choices in §3. |
| Hermes app UUID change | If the `hermes` Coolify app is ever recreated, its network name changes. Using the shared `coolify` network (§6) avoids inheriting the fragility already documented for Mnemosyne's bind-mount path. |

---

## 12. Graduation

When this ships and the evaluation concludes, this document is superseded by
`docs/cognee-operations.md`, written to mirror `docs/mnemosyne-operations.md` —
component inventory, UUIDs, endpoints, and the failure modes actually encountered.

# Mnemosyne Operations

Operational handoff for the shared SQLite memory layer behind the hermes gateway,
Claude Desktop, and the read-only dashboard.

**Live state verified 2026-09-14.** Everything below marked "verified" was checked
against the running containers on that date. Everything else comes from the session
history in Serena memories and git history.

---

## 1. Orientation

Mnemosyne is a local-first SQLite memory provider. We run it as the hermes agent's
memory backend, replacing the earlier hindsight and cognee providers. Three separate
processes read and write **one physical SQLite file** on the Coolify host, so the
agent, Claude Desktop, and a web viewer all see the same memories.

Two things are unusual about this deployment and drive most of the complexity:

1. **Embeddings are remote.** The `[embeddings]` extra (fastembed + ONNX) is
   deliberately not installed. Embedding generation goes to OpenRouter's
   OpenAI-compatible `/embeddings` endpoint. This keeps CPU off the home server,
   which previously crashed under local inference. `sqlite-vec` still does the
   similarity search locally.
2. **We run five build-time monkey-patches.** Each fixes a real upstream bug we
   diagnosed ourselves. They are anchor-based string surgery on installed
   site-packages files, and they fail silently (warn, do not error) if an anchor
   stops matching. See section 5.

---

## 2. Architecture

```
  hermes gateway            mnemosyne-mcp             mnemosyne-dashboard
  (in-process plugin)       (standalone SSE)          (read-only web viewer)
  container: /opt/data/     container: /data          container: /data/mnemosyne
    mnemosyne/data                                      opens with ?mode=ro
        |                         |                         |
        +-------------------------+-------------------------+
                                  |
              HOST: /data/coolify/applications/
                    tgg4k0sc8wgocck08cc4s4cg/.hermes/mnemosyne/data
                                  |
                +-----------------+------------------+
                |                                    |
        mnemosyne.db                        shared/mnemosyne.db
        (default bank, ~95 MB)              (shared surface, ~2 MB)
```

**The single most important fact:** all three containers bind-mount the same host
directory at different container paths. If you change one side's path, the store
silently forks into two files and cross-agent recall stops working without any error.

Coolify rewrites the `~/.hermes` in `docker-compose.yaml` to the per-app path shown
above. **If the hermes Coolify app is ever recreated with a new UUID, the hardcoded
path in the mnemosyne-mcp repo's compose file must be updated by hand.**

### Two separate banks

These are genuinely different features, and conflating them has cost time before.

| Bank | File | Tools | Purpose |
|---|---|---|---|
| Default | `mnemosyne.db` | `mnemosyne_remember` / `_recall` and the rest | Ambient automatic capture and per-turn recall |
| Shared surface | `shared/mnemosyne.db` | `mnemosyne_shared_remember` / `_recall` / `_forget` / `_stats` | Explicit "put this where the other agent can see it" layer |

---

## 3. Component inventory

| Component | Repo | Coolify app UUID | Endpoint |
|---|---|---|---|
| hermes gateway | `aaka3207/hermes` (this repo, branch `main`) | `tgg4k0sc8wgocck08cc4s4cg` | internal, API on 8642 |
| mnemosyne-mcp | `aaka3207/mnemosyne-mcp` | `kbi92k8clrh7977toxxggsou` | `https://mnemosyne-mcp.aakashe.org/sse` |
| mnemosyne-dashboard | sub-service in this repo's compose | (same app as hermes) | `https://mnemosyne.aakashe.org` |
| metamcp aggregator | separate app | `vkokwgggswokogg484oww0gc` | `https://metamcp.aakashe.org/metamcp/mnemosyne-endpoint/mcp` |

The dashboard is not a separate Coolify app. It is a service inside this repo's
`docker-compose.yaml`, so it gets recreated on every hermes redeploy and its
container name changes each time. Always rediscover the name, never hardcode it.

The dashboard runs upstream `wysie/mnemosyne-dashboard` pinned to commit
`42ba6b710e339d4c670e79874b7138f1c0f4be2e`. It is a single-maintainer repo, hence
the pin. It is cloned at container boot, not baked into an image.

---

## 4. Where configuration lives

This is the mental model that saves the most time. There are four layers, and
knowing which one a setting lives in tells you what it takes to change it.

| Layer | Contains | To change |
|---|---|---|
| **Dockerfile** | package pins, the six patches, plugin.yaml shim, PATH shim | edit, commit, push, full rebuild |
| **docker-compose.yaml** | `MNEMOSYNE_*` env vars, mounts, healthchecks, resource limits | edit, commit, push, redeploy (no rebuild) |
| **Coolify UI** | secrets and the extraction prompt, reaches container via `env_file: .env` | edit in UI, restart (no rebuild, no commit) |
| **`hermes config`** | `/opt/data/config.yaml` on the volume: provider selection, shared surface paths, ignore patterns | `hermes config set <key> <value>`, restart |

### Key settings by layer

**Dockerfile**
- `mnemosyne-memory==3.15.1`, `sqlite-vec==0.1.9`
- The six patches (section 5)
- Hand-written `plugin.yaml` into the symlinked provider dir

**docker-compose.yaml** (hermes service)
- `MNEMOSYNE_DATA_DIR=/opt/data/mnemosyne/data`
- `MNEMOSYNE_EMBEDDINGS_VIA_API=true`
- `MNEMOSYNE_EMBEDDING_API_URL=https://openrouter.ai/api/v1`
- `MNEMOSYNE_EMBEDDING_API_KEY=${OPENROUTER_API_KEY}`
- `MNEMOSYNE_EMBEDDING_MODEL=openai/text-embedding-3-small`
- `MNEMOSYNE_EMBEDDING_DIM=1536`
- `MNEMOSYNE_SYNC_ROLES=user`
- `MNEMOSYNE_AUTHOR_ID=hermes`, `MNEMOSYNE_AUTHOR_TYPE=agent`

**Coolify UI**
- `MNEMOSYNE_EXTRACTION_PROMPT` — the custom instruction steering LLM fact
  extraction toward durable, portable, user-centric facts and away from session,
  codebase, log, and debug detail. Deliberately not in the repo so it is tunable
  without a rebuild.
- `OPENROUTER_API_KEY`, `MNEMOSYNE_MCP_TOKEN`, dashboard password hash
- **Note:** there are two copies of the extraction prompt, a production one and a
  preview one, and their wording has drifted. Only production is in effect.

**`hermes config` / `/opt/data/config.yaml`** (verified)
```yaml
memory:
  memory_enabled: true
  provider: mnemosyne
  memory_char_limit: 2200
  user_char_limit: 1375
  nudge_interval: 10
  mnemosyne:
    auto_sleep: true
    sleep_threshold: 100
    shared_surface_path: /opt/data/mnemosyne/data/shared/mnemosyne.db
    shared_surface_read: true
    ignore_patterns: '(?is)^You are a bounded.*Gmail monitor ...'
```

`ignore_patterns` suppresses capture of the recurring bounded Gmail monitor prompts,
which would otherwise flood the store with identical system text.

---

## 5. The six patches

All are idempotent. **They are not all equally safe.** Patches 1-4 are anchor-based
and *warn rather than fail* when their anchor stops matching -- a silently skipped
patch is a live risk on every base-image change, and the base is `:latest`, so that
is every rebuild. Patches 5b and 6 are **fail-closed**: they exit non-zero and fail
the image build rather than degrade quietly.

**This risk is not theoretical.** On 2026-09-14 upstream reflowed the photon
`_MIRROR_FILES` tuple onto a single line. The old patch 5 anchor stopped matching,
warn-and-skipped, and the build succeeded -- shipping an image with the sidecar fix
missing, into production, unnoticed. See "The patch 5 fail-open incident" below.

**This is now backstopped.** A consolidated *patch gate* runs as the last patch
step of every build and fails the build unless all six patches are verifiably in
effect. It asserts invariants, not anchors, and prefers functional checks -- it
calls `extract_and_store_facts()` and requires `{}`, writes a memory and requires
`author_id` to be stamped, imports `sidecar_paths.py` and reads the real
`_MIRROR_FILES`. Patches 1-4 keep their warn-not-fail apply steps (converting each
individually risks false alarms when upstream fixes something); the gate is what
makes a silent skip impossible.

Verified by deliberately breaking each of the six patches and confirming the gate
fails with the right specific message, and by simulating an *upstream fix* and
confirming it still passes. Build output to expect:

```
#14 compatibility patch gate PASSED -- all patches verified in effect
```

If the gate ever fails, read which check failed before touching anything: a
FAIL means either the patch died (fix the patch) or upstream changed the
contract (update the invariant). Never delete a check to make a build green.

### In this repo's Dockerfile

| # | Target | What it does | Why needed |
|---|---|---|---|
| 1 | `openai/lib/_parsing/_responses.py` | Guards `response.output` against null | The ChatGPT Codex backend streams `output=None`, violating the OpenAI contract. Crashes every Codex call including all memory ops. |
| 2 | `mnemosyne/core/local_llm.py` | Lazily re-registers the Hermes host LLM backend | The gateway does not keep the backend registered for extraction. Without it `extract_facts()` returns empty and the custom extraction prompt is never consulted. |
| 3 | `mnemosyne/core/beam.py` | No-ops `BeamMemory.extract_and_store_facts` | Mnemosyne runs two extractors. The regex one has no config gate and scrapes "first/then" fragments and imperatives into the store as facts. We keep the LLM one, kill the regex one. |
| 4 | `hermes_memory_provider/__init__.py` | Author attribution, two changes | Stamps writes with `author_id`, and separately forces automatic recall's `author_id` to `None` so recall stays shared. |
| 5b | `plugins/platforms/photon/sidecar_paths.py` | Ensures every local `./*.mjs` that `index.mjs` imports appears in `_MIRROR_FILES` | Unrelated to memory. Photon sidecar crashed on boot with `ERR_MODULE_NOT_FOUND`. **Rewritten 2026-09-14** from a hardcoded-filename string anchor to an invariant check, after the anchor silently died. Fail-closed. |
| 6 | `hermes_memory_provider/__init__.py` | Replaces canonical model-slot ranking: stopwords + IDF + name-boost + relevance floor, and may return an empty block | Upstream ranked by raw token-overlap count and *always* filled 3 slots. A query sharing only `does` and `user` with an unrelated medical fact injected it. Runs on every non-trivial turn. Fail-closed, with a build-time behavioral self-test. |

**Patch 4 is the subtle one.** Mnemosyne couples write-stamping and recall-scoping
through the same field. Setting `author_id` on the BeamMemory constructor makes
`_prefetch_bank()` pass it into `recall()`, which triggers a clause that filters to
that author *and* skips session scoping. Left alone, every automatic recall would
narrow to hermes-authored rows only, and the 87 pre-existing NULL-author rows would
become invisible. The patch does both halves on purpose. Do not "simplify" it.

**Patches 5b and 6 check invariants, not text.** This matters when upstream ships
constantly. 5b derives the required file list from `index.mjs`'s actual imports, so
it survives reformatting, picks up newly added sidecar modules automatically, and
becomes a clean no-op if upstream ever fixes the list themselves (rather than failing
the build over a bug that no longer exists). 6 verifies behavior, not source text.
Prefer this shape for any future patch.

### The patch 5 fail-open incident (2026-09-14)

Worth reading before changing any patch.

- Upstream reflowed `_MIRROR_FILES` from a multi-line tuple to one line.
- The old anchor matched the multi-line form only -> 0 matches.
- The patch printed `WARNING ... skipping` and the build **succeeded**.
- The resulting image shipped to production without the photon fix.
- `index.mjs` still imported `send-format.mjs` and `stream-staleness.mjs`, so the
  `ERR_MODULE_NOT_FOUND` crash conditions were fully restored.
- It did not actually crash, for two incidental reasons: the install tree was
  writable, and `/opt/data/photon/sidecar` still held both `.mjs` files copied
  there by an older, correctly-patched image. **Borrowed state, not correctness.**
- Detected only because a verification build was run for an unrelated patch.

Lesson: a warn-not-fail patch is indistinguishable from a working one unless you
read the build log or assert the invariant. Assert the invariant.

### Patch 6 tuning knobs (runtime, no rebuild)

| Env var | Default | Effect |
|---|---|---|
| `MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_IDF` | `2.0` | Relevance floor. 2.5 measured identical; 3.0 starts dropping legitimate career slots. |
| `MNEMOSYNE_PREFETCH_MODEL_SLOT_NAME_BOOST` | `2.0` | Weight multiplier for query terms matching a slot's name/category. |
| `MNEMOSYNE_PREFETCH_MODEL_SLOT_LIMIT` | `3` | Max slots injected. Do **not** raise until contradictory-slot detection exists. |
| `MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_OVERLAP` | `1` | Legacy gate, still honored. **Do not set to 2** -- measured strictly worse (see below). |

**Rejected fixes, measured, do not retry:**

1. `MIN_OVERLAP=2` -- the junk match is itself 2 tokens (`does` + `user`) so it
   survives, while genuine single-specific-token matches are pruned: career 3->0,
   dinefile 3->0. Strictly worse.
2. Stopwords alone -- evicts the bad slot, then backfills the freed slots with
   equally irrelevant ones (always-fill) and drops a good hit. Net neutral.
3. IDF alone -- IDF measures corpus *rarity*, not meaninglessness. Across 22 slots
   `does` has df=2 (idf 2.04) vs `user` df=5 (idf 1.34), so IDF *rewards* the rare
   function word. Fails at every floor up to 2.5.

Only the combination works.

### In the mnemosyne-mcp repo's Dockerfile

| Target | What it does |
|---|---|
| `mnemosyne/core/beam.py` | Same regex-extractor no-op as patch 3. Required because this container writes to the same shared DB, so without it the Claude Desktop side would keep polluting a store the hermes side just cleaned. |

That repo also sets `PYTHONPATH=/usr/local/lib/python3.11/site-packages/integrations/hermes/src`
because mnemosyne ships the MCP tool schemas at a non-importable path. Without it the
server starts fine and advertises **zero tools**.

Two SSE patches were **deleted** at the 3.15.1 bump because upstream fixed both
natively. Do not reintroduce them.

---

## 6. Hurdles already solved

Catalog of problems hit and fixed, so the next agent does not rediscover them.
All are resolved unless marked otherwise.

1. **Provider never registered.** Mnemosyne ships no `plugin.yaml`, and hermes skips
   any `plugins/memory/<name>/` directory lacking one. Symptom was "mnemosyne plugin
   not found" despite the package importing fine. Fixed by writing a `plugin.yaml`
   into the symlinked package dir at build time.
2. **Provider not activated.** Hand-editing `config.yaml` left `memory.provider: ''`.
   Use `hermes config set memory.provider mnemosyne`.
3. **Wrong embedding env vars.** Mnemosyne reads `MNEMOSYNE_EMBEDDING_API_KEY` and
   `MNEMOSYNE_EMBEDDING_API_URL`, **not** `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL`.
   Without the right names, `available()` returns false and it silently degrades to
   FTS5 keyword-only search with no error.
4. **Vector dimension baked into the schema.** The DB was first initialized under the
   default bge-small model, creating `vec_*` tables as `int8[384]`. The 1536-dim API
   vectors do not fit, so semantic recall broke. The only fix was wiping the DB and
   letting it recreate at 1536. **If you ever change the embedding model, the
   dimension must match or the store must be rebuilt.**
5. **Shared file, wrong UID.** mnemosyne-mcp must run as `user: "10000:10000"` to
   match hermes, or its WAL and SHM files land root-owned and hermes gets "database
   is locked".
6. **UID 10000 has no passwd entry,** so `expanduser("~")` resolves to `/`. Set
   `HOME=/tmp` in that container.
7. **Dashboard needs read-write mount.** SQLite cannot open a WAL database on a
   read-only directory because it must touch `-shm`. The mount is rw on purpose; the
   `?mode=ro` connection string enforces query-only at the SQLite layer.
8. **Zero tools over MCP.** Fixed by the `PYTHONPATH` entry described above.
9. **SSE healthcheck flapping.** The server 401s every HTTP route without a token, so
   an HTTP probe always reads unhealthy. Uses a TCP socket probe instead.
10. **Traefik hung every proxied request.** The container sits on three Docker
    networks; Traefik needs `traefik.docker.network=coolify` to know which to dial.
    The container's own endpoint was healthy the whole time, only the proxy dial was
    broken.
11. **Cloudflare redirect loop.** Flexible SSL plus origin force-https. Other services
    use the `http://` scheme in Coolify domains.
12. **Junk facts from assistant turns.** Extraction was mining assistant replies and
    hermes's own system prompt (tool procedures, injection defenses) into "facts".
    Fixed with `MNEMOSYNE_SYNC_ROLES=user` plus a one-time cleanup that cleared
    `memoria_facts` and `memoria_instructions` entirely.
13. **Shared surface never worked on either side.** The hermes side defaulted its path
    into site-packages (unwritable, caught and logged as an init failure). The
    mnemosyne-mcp side defaulted it under `HOME=/tmp` (ephemeral, and a different file
    anyway). Fixed by pinning both to the same bind-mounted path. Verified by matching
    inode from both containers.
14. **Scope matters even when the file is shared.** The two sides use different fixed
    `session_id` values. A plain `remember()` defaults to `scope="session"` and stays
    invisible across the mismatch. The real handler passes `scope="global"` for exactly
    this reason. A "zero results" test that skipped this looked like a real bug.
15. **The 3.15.1 upgrade was thought to be blocked** by upstream issue #624. It was
    not a mnemosyne bug. It was the hermes base image's rolling `uv exclude-newer = "14 days"`
    window, which ages out on its own.
16. **Gateway healthcheck aborting deploys.** The gateway takes roughly 110s to print
    its banner and longer to bind 8642. `start_period` is 300s. The dashboard's boot
    (apt plus git clone) added enough contention to push it over, so the dashboard now
    gates on `depends_on: hermes healthy`.

---

## 7. Current live state (verified 2026-09-14)

All four containers healthy. hermes and its sub-services restarted recently;
mnemosyne-mcp had been up three weeks.

**Version:** `mnemosyne-memory 3.15.1` in the hermes venv.

**Store contents:**

| Table | Rows |
|---|---|
| `working_memory` | 917 |
| `episodic_memory` | 224 |
| `consolidation_log` | 369 |
| `memoria_preferences` | 7 |
| `memoria_facts` | 0 |
| `memoria_instructions` | 0 |
| `triples` | 1 |
| shared surface `working_memory` | 1 |

**Author split:** `hermes` 828, `claude-desktop` 2, NULL 87. The NULL rows predate
the author-attribution patch. Growth from 272 working memories on 2026-08-22 to 917
confirms automatic capture is working.

`memoria_facts` and `memoria_instructions` sitting at zero is expected. That is the
regex extractor staying disabled since the August cleanup, not a failure.

**Health:** embeddings available, `sqlite-vec` available, `vec_working` coverage
complete at 984 rows with 0 missing. `fastembed` reports MISSING, which is deliberate.

**`hermes mnemosyne doctor` reports "16/43 checks passed", which is misleading.** Most
of those 43 lines are informational values, not pass/fail assertions. Do not treat
that ratio as a problem signal. Also note the doctor's auto-fix tries to pip-install
fastembed, which we do not want and which fails harmlessly since pip is not on PATH.

**Files:** main DB 95 MB plus a 5.7 MB WAL, shared DB 2.1 MB, all owned `hermes:hermes`.

---

## 8. Open issues

| Issue | Status |
|---|---|
| **Orphaned support rows.** 1,961 of 3,125 gists and 1,761 of 2,701 fallback embeddings have no surviving parent memory. | Not fixed, and **lower priority than it looks.** Verified 2026-09-14: both vector recall paths INNER JOIN against `working_memory`/`episodic_memory` and filter `superseded_by`/`valid_until` *before* `LIMIT`, so orphans can never surface in recall and never consume the result budget. Wasted disk and scan time only, not a correctness or relevance defect. Caused by non-cascading deletes; no FKs are declared on these tables. |
| **`dense_score: 0.0`** on working-memory recall for most rows. | Fixed upstream in PR #705, merged 2026-08-17, **not in 3.15.1**. This is the single strongest reason to eventually upgrade. |
| **mnemosyne-mcp shared-surface writes land with NULL author_id.** `_create_surface_instance()` does not pass the author env vars, unlike the regular path. | Known, not fixed, low priority. |
| **metamcp caches its tool list.** After any mnemosyne-mcp redeploy, metamcp returns "Unknown tool" until you reconnect via `/mcp` in Claude Code. | Recurring, metamcp-side, not investigated. |
| **Two copies of the extraction prompt exist (production + preview) with different wording.** | **Not drift.** Coolify auto-creates a preview-scoped twin whenever you create a production env var; deleting the production entry leaves the twin behind. Only production is in effect (this app deploys from `main` and gets no preview deployments). Harmless. The preview wording shows the literal JSON skeleton and may actually be the better prompt -- promoting it to production is a one-field, reversible change nobody has made. |
| **Off-box backups.** The July 2026 disk failure happened because backups lived on the failing drive. | Still outstanding across the whole Coolify host, not just mnemosyne. |
| ~~**Patches 1-4 are still fail-open.**~~ **RESOLVED 2026-09-14** by the consolidated patch gate (section 5), which fails the build unless every patch is verifiably in effect. | Was the highest-priority structural risk. |
| **The durable corpus is tiny and barely grows.** 943 working memories, but only 78 are `scope='global'`; plus 41 global episodic and 22 injectable canonical model slots. ~141 items total reachable by cross-session recall. | **This is the real "memory feels useless" cause** -- scarcity, not noise. 838 conversation autosave rows are `scope='session'` by design and correctly never surface cross-session. Nothing promotes memories to global automatically except `sleep_model_refresh` (see below) and a ten-phrase identity matcher that has never once fired (0 rows). Hermes made 61 deliberate global writes against ~800 autosaves. **The lever is agent write-rate (soul.md), not Mnemosyne config.** |
| **Contradictory canonical slots can co-inject.** `TV mode lighting` ("turn off all lights except the table lamp") and `TV light combo` ("turn on the TV lights while keeping the bedroom lights on") both inject for a TV query. | Not fixed. No contradiction detection exists in model-slot prefetch. Patch 6 does not make it worse but does not solve it. **Do not raise `MODEL_SLOT_LIMIT` until this is handled.** |
| **`sleep_model_refresh_enabled: false` in `config.yaml` is INERT.** | Upstream bug. `model_refresh.sleep_model_refresh_enabled()` reads **only** the env var `MNEMOSYNE_SLEEP_MODEL_REFRESH_ENABLED` (default `True`) and never consults the config file -- even though the key is registered in the config ENV_MAP with a default. Someone set it on 2026-09-02 intending to disable model refresh; it has been running the whole time. Verified: env var unset in both containers, `sleep_model_refresh_enabled() -> True`. **If you ever really need it off, use the env var.** Left enabled deliberately: it is the only working durable-fact promoter, its output is high quality, and it leaks no secrets. |
| **`MNEMOSYNE_WM_TTL_HOURS=4` does not prune old rows.** 213 unconsolidated rows, 144 of them older than 4h. | Working as designed, documented so nobody "fixes" it. `_trim_working_memory` deletes only rows with `consolidated_at IS NULL` **scoped to the active session**; 731 rows are consolidated and exempt by the "originals stay" contract. With 210 distinct sessions, a session's stale rows become unreachable by trim once that session ends. Structural, minor. |

---

## 9. Versions and upgrade path

We pin `mnemosyne-memory==3.15.1` (published 2026-07-30) in both repos.

**There has been no stable release since.** The publisher moved the repo from the
personal `AxDSan` account to the `mnemosyne-oss` organization and set the next version
to 4.0.0, shipping betas instead of 3.x point releases:

| Version | Date | Type |
|---|---|---|
| 3.15.1 | 2026-07-30 | stable, what we run |
| 4.0.0b1 | 2026-08-24 | beta |
| 4.0.0b2 | 2026-09-10 | beta |

PyPI publishing is controlled solely by Abdias J. Moya (`AxDSan`). Denis Hache
(`dplush`) is co-maintainer with merge rights but no publish rights.

**Do not confuse `mnemosyne-hermes` with `mnemosyne-memory`.** The GitHub release
tagged `v0.7.0` (2026-09-09) is the standalone `mnemosyne-hermes` plugin package,
which we do not install. We use the `hermes_memory_provider` module bundled inside
`mnemosyne-memory`. The standalone package's provider code has drifted and no longer
matches our patch anchors, and it pulls the `[embeddings]` extra we deliberately omit.

### What a 4.0 upgrade would involve

Checked against 4.0.0b2 in a scratch venv on 2026-09-14:

- **All patch anchors still match exactly once.** None of the four underlying bugs are
  fixed upstream, so all patches stay necessary.
- **4.0 ships its own `plugin.yaml`** inside the provider package. Our Dockerfile shim
  would overwrite it, and the upstream manifest uses a different key set. Make the
  shim conditional or drop it and test discovery with `hermes doctor`.
- **Schema change is additive only:** two new tables, `media_assets` and
  `media_moments`. A 4.0 writer should not break a 3.x reader, but upgrade both
  deployments in the same window anyway.
- **Unknown embedding models now fail loud at startup** instead of silently assuming
  384 dimensions. Our model and dimension vars must be correct or the container will
  refuse to start. This is an improvement, but it turns a silent degradation into a
  hard failure.
- **Relevant fixes we would gain:** the `dense_score` fix (#705), plugin tools no
  longer talking to a second uninitialized provider, host backend retention, cascading
  deletes for the orphan problem, and a native MCP Streamable HTTP transport.

**Recommendation: stay on 3.15.1 until 4.0.0 goes stable.** A fork plan exists at
`~/.claude/plans/synthetic-percolating-fox.md` if consuming merged-but-unreleased
fixes ever becomes worth the maintenance. Only its verification step was executed.

### The `aaka3207/mnemosyne` fork is NOT used

There is a fork of `mnemosyne-oss/mnemosyne` at `aaka3207/mnemosyne`, created
2026-08-07. **Nothing depends on it.** Verified 2026-09-14:

- It is 0 commits ahead of upstream main and 340 behind. It carries none of our
  patches and no custom work. Its branches are upstream's, copied at fork time.
- It exists because one upstream PR was opened from it: #652,
  "test(mcp): cover SSE messages mount lifecycle", merged 2026-08-09 and credited in
  the v0.7.0 release notes. It was a contribution fork, not a deployment source.
- Neither Coolify app builds from it. The hermes app builds `aaka3207/hermes`, the MCP
  app builds `aaka3207/mnemosyne-mcp`, and both Dockerfiles pip-install
  `mnemosyne-memory` from PyPI. The engine never comes from a git source.

Do not mistake this fork for a live dependency, and do not assume it holds our patches.

---

## 10. Runbook

### Find the containers

Names change on every redeploy. Always rediscover.

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -iE 'hermes|mnemo'
H=$(docker ps --format '{{.Names}}' | grep '^hermes-tgg' | head -1)
```

### Check version and provider

```bash
docker exec $H sh -lc 'grep -A6 "^memory:" /opt/data/config.yaml'
docker exec $H /opt/hermes/.venv/bin/python -c \
  'import importlib.metadata as m; print(m.version("mnemosyne-memory"))'
```

### Row counts and author split

Keep this on one line. Multi-line heredocs get flattened when sent over ssh-mcp and
fail with an IndentationError.

```bash
docker exec $H /opt/hermes/.venv/bin/python -c 'import sqlite3;c=sqlite3.connect("/opt/data/mnemosyne/data/mnemosyne.db");print([(t,c.execute("select count(*) from "+t).fetchone()[0]) for t in ["working_memory","episodic_memory","memoria_preferences","triples"]]);print(c.execute("select author_id,count(*) from working_memory group by author_id").fetchall())'
```

### Diagnostics

```bash
docker exec $H sh -lc 'hermes mnemosyne doctor'
```

Full JSON log lands at `/opt/data/mnemosyne/logs/diagnose_<date>.jsonl`.

### Reload MCP config

Editing `mcp_servers.*` does **not** propagate to running processes. Two separate
processes hold their own MCP connections and both must be restarted:

```bash
docker exec $H /command/s6-svc -r /run/service/gateway-default
docker exec $H /command/s6-svc -r /run/service/dashboard
```

Restarting only the gateway leaves desktop chats on the old config. This has bitten
us before.

### Change the extraction prompt

Edit `MNEMOSYNE_EXTRACTION_PROMPT` in the Coolify UI for app
`tgg4k0sc8wgocck08cc4s4cg`, then restart. No rebuild, no commit.

### Reset the dashboard password

The password is a PBKDF2-SHA256 hash at 200,000 iterations with a 32-char hex salt,
stored in `plugin-data/mnemosyne-dashboard/config.json` on the volume. It cannot be
recovered, only regenerated. Exec into the dashboard container, generate a new
password and salt, write `password_hash`, `password_salt`, and `auth_enabled: true`
into that JSON, restart the container, and verify against `POST /api/auth/login`
(note: `/api/login` 404s).

### Rebuild vs restart

- Dockerfile change: commit, push, full Coolify deploy.
- compose or Coolify env change: redeploy or restart, no rebuild.
- **Careful:** for this Dockerfile-based compose app, Coolify's "restart" actually
  triggers a full rebuild-through-deploy pipeline taking 4 to 5 minutes and producing
  a new container name. It is not a lightweight process restart.

### Wipe and reinitialize the store

Only if the vector dimension must change. Stop both writers, delete `mnemosyne.db*`
and the `banks/` directory, confirm `MNEMOSYNE_EMBEDDING_DIM` is correct, then let
hermes recreate the schema on next boot.

---

## 11. Rules for the next agent

- **Never change the shared host path on one side only.** The store silently forks
  and nothing errors.
- **Read the build log after any rebuild.** All five patches warn rather than fail.
  Look for the explicit "patched ... OK" lines.
- **Keep the embedding model and dimension identical across both deployments.** They
  write the same vector table.
- **Do not reintroduce the deleted SSE patches** in mnemosyne-mcp or the
  `mcp==1.29.0` pin. Both are upstream-fixed and the pin now actively conflicts.
- **Do not "fix" the author-attribution patch's forced `None`.** It is load-bearing.
  See section 5.
- **Do not trust the doctor's pass ratio.** Read the individual findings.
- **Secrets stay out of this file and out of Serena memories by design.** They live
  in the Coolify UI and in `config.json` on the volume.
- **When testing shared-surface behavior, pass `scope="global"`.** Otherwise a correct
  setup looks broken.
- **`/sc:load` plus the Serena memories** named `mnemosyne-*`, `hermes/*`, and
  `sessions/*` carry the full narrative history behind this document.

# hermes (self-hosted deployment)

A customized deployment of [NousResearch Hermes Agent](https://github.com/NousResearch/hermes-agent),
running as a personal assistant reachable over Discord. This repo is **not** a fork of the agent —
it's a thin layer on top of the upstream `nousresearch/hermes-agent` image that adds a few
packages, a Syncthing sidecar, voice support, and an alternate memory provider, then wires it all
up for deployment via Coolify.

> The agent code itself lives in the upstream image. This repo only owns the `Dockerfile`,
> `docker-compose.yaml`, and a couple of content dirs (`hub/`, `skills/`). Everything in
> `/opt/hermes/` inside the container comes from upstream.

---

## Architecture

```
                     ┌───────────────────────────────────────────┐
                     │  Coolify (build + deploy + reverse proxy)   │
                     └───────────────────────────────────────────┘
                                        │ builds Dockerfile, runs compose
              ┌─────────────────────────┴───────────────────────────┐
              ▼                                                       ▼
  ┌───────────────────────────┐                       ┌──────────────────────────┐
  │  hermes (this image)       │                       │  syncthing (linuxserver)  │
  │  - gateway run             │   shared bind mount   │  PUID/PGID 10000          │
  │  - API server :8642        │◄────  ~/.hermes  ────►│  syncs the data dir to    │
  │  - dashboard  :9119        │   (host) → /opt/data  │  your other machines      │
  │  - Discord adapter         │       (hermes)        │       → /data1            │
  │  - memory provider         │                       └──────────────────────────┘
  │  - voice (STT/TTS)         │
  └───────────────────────────┘
```

- **`hermes`** — built from this `Dockerfile`. Runs the gateway, the API server, the Discord
  adapter, the active memory provider, and voice. Container user is `hermes` (UID **10000**),
  whose `$HOME` and `$HERMES_HOME` are both `/opt/data`.
- **`syncthing`** — `lscr.io/linuxserver/syncthing`, mounts the **same** `~/.hermes` host
  directory and syncs it to other devices. Runs as PUID/PGID **10000** to match the hermes user.
- **Shared volume** — `~/.hermes` on the host is bind-mounted into **both** containers
  (`/opt/data` in hermes, `/data1` in syncthing). This is the single source of truth for config,
  memory databases, auth tokens, and caches. It persists across redeploys.

Two domains front the app via the Coolify reverse proxy:
- `hermes-dashboard.aakashe.org` → dashboard (port 9119)
- `hermes-api.aakashe.org` → gateway API (port 8642)

---

## What this image adds on top of upstream

Everything here is in the `Dockerfile`, layered onto `nousresearch/hermes-agent:latest`.

| Addition | Why | Notes |
|----------|-----|-------|
| `syncthing` (apt) | Syncthing CLI available inside the image | The actual sync runs in the sidecar container; this is the binary |
| `@anthropic-ai/claude-code` (npm, global) | Lets the agent shell out to Claude Code | |
| `byterover-cli` (npm, global) | `brv` — portable memory/context CLI | Configured at deploy time (see below) |
| `hindsight-client==0.6.1` (uv pip) | Client for the self-hosted Hindsight memory server | A selectable memory provider |
| `faster-whisper==1.2.1` (uv pip) | Local, free speech-to-text for Discord voice | Pinned to the exact version Hermes' lazy-deps expects |
| `mnemosyne-memory[embeddings]==3.0.0` + `sqlite-vec==0.1.9` (uv pip) | Local-first SQLite memory provider with semantic vector search | Bundled as a first-class provider (see Memory) |
| **openai SDK null-guard** (build-time patch, [#17](https://github.com/aaka3207/hermes/pull/17)) | Stops every Codex call from crashing on a null `response.output` | Patches the installed `openai` SDK. **Temporary** — remove when fixed upstream (see Operational notes) |
| **Mnemosyne host-LLM lazy-register** (build-time patch, [#18](https://github.com/aaka3207/hermes/pull/18)) | Makes Mnemosyne actually route memory ops through Codex instead of falling back to lossy non-LLM summaries | Patches the installed `mnemosyne` package. **Temporary** — remove when fixed upstream (see Operational notes) |

### Why packages are baked into the image, not installed at runtime

The container's Python venv (`/opt/hermes/.venv`) lives **inside the image** and is recreated on
every redeploy. Anything installed at runtime (e.g. Hermes' own lazy-deps, or a `pip install` in a
shell session) lands in that ephemeral venv and is **lost on the next deploy**. So every Python
dependency we rely on is pinned in the `Dockerfile`. Persistent *data* (memory DBs, model caches,
auth) lives on the `~/.hermes` volume instead.

### Tooling notes / gotchas baked into the Dockerfile

- **Use `uv pip install`, not `uv sync`.** The upstream venv is built with `uv sync --extra all
  --extra messaging`. `uv sync` reconciles the venv to *exactly* the requested extras — so running
  it again with different extras silently **removes** the messaging (Discord) adapters. Adding a
  package = `uv pip install`, which is additive. (This caused an early outage; see git history
  around PR #6–#9.)
- **The venv has no `pip`.** uv-created venvs don't ship a `pip` binary. Use
  `uv pip install --python /opt/hermes/.venv/bin/python …`.
- **Mnemosyne is symlinked into the image's bundled plugin dir**, not the volume. The upstream
  Mnemosyne installer symlinks `$HERMES_HOME/plugins/mnemosyne` (the volume) → site-packages (the
  image) at a python-version-specific path, which dangles if the base image bumps Python. We link
  *inside* the image (`/opt/hermes/plugins/memory/mnemosyne`), resolving the source dynamically via
  `importlib.find_spec`, so it's stable across redeploys.

---

## Memory

Hermes runs **one external memory provider at a time**, selected by `memory.provider` in
`config.yaml`. Built-in `MEMORY.md`/`USER.md` is always active alongside it.

Providers available in this image (discoverable via `hermes memory status`):

| Provider | Type | Notes |
|----------|------|-------|
| **mnemosyne** | local SQLite + vectors | **Currently active.** Zero-cloud, sub-ms. See below. |
| hindsight | self-hosted server | Points at `http://hindsight-api.aakashe.org` (`mode: local_external`). Bank config + missions tuned server-side. |
| honcho, mem0, byterover, holographic | cloud / various | Bundled by upstream; selectable if configured. |

### Mnemosyne (active provider)

[Mnemosyne](https://github.com/AxDSan/Mnemosyne) is a local-first memory system: SQLite with
`sqlite-vec` vector search + FTS5 keyword search, all on the `~/.hermes` volume. No cloud, no API
keys for storage.

- **Data**: `/opt/data/mnemosyne/data/mnemosyne.db` (set explicitly via `MNEMOSYNE_DATA_DIR` in
  compose — the explicit path avoids `~/.hermes` resolving to ephemeral `/root/.hermes` under a
  root context).
- **Embeddings**: `fastembed` running `BAAI/bge-small-en-v1.5` (384-dim ONNX, quantized). Model
  downloads once (~65 MB) to `/opt/data/.hermes/cache/fastembed` on the volume.
- **LLM** (for sleep-cycle summarization / fact extraction): routed through Hermes' auxiliary
  client → resolves to the **`openai-codex`** provider (the Codex subscription), with OpenRouter
  `deepseek/deepseek-v4-flash` as fallback. No local LLM tier is installed (the heavy
  `llama-cpp`/`ctransformers` extra is intentionally omitted).
- **How memory flows**: conversation turns → working memory (FTS-searchable) → consolidated into
  episodic memory + vectors during a `sleep` cycle (auto at the working-memory count threshold, or
  manual `hermes mnemosyne sleep`). Working memories must age past `MNEMOSYNE_WM_TTL_HOURS` before
  they're consolidation-eligible.
- **Triple/KG extraction is regex-based** (zero-LLM, fast but crude). Relationships improve over
  time in *currency* (temporal supersession), *density* (graph accumulates edges), and *confidence*
  (veracity consolidation) — but a clumsy individual parse is not retroactively re-parsed. Clean,
  agent-curated `mnemosyne_remember` calls produce higher-quality facts than the auto-extractor.

Useful commands (from inside the container, as the `hermes` user):
```bash
hermes mnemosyne stats          # working/episodic counts, vec_type
hermes mnemosyne doctor --no-fix # health of sqlite-vec, fastembed, etc.
hermes mnemosyne sleep           # consolidate working → episodic (+vectors)
hermes mnemosyne inspect "query" # search memories
```

### Switching providers

Edit `config.yaml` `memory.provider` (see config notes below for *how* to edit safely), then
either restart hermes or let it re-read on its polling cycle. Switching does **not** migrate data —
each provider has its own store. To carry knowledge across, either re-state durable facts in
conversation (cleanest) or use a provider importer where one exists.

---

## Voice (Discord)

Voice is config-driven; the dependencies are baked into the image.

- **STT**: local **faster-whisper** (`stt.provider: local`, model `base`). Free, private,
  no API key. Model downloads to the volume on first use. Discord audio decode uses libopus +
  PyNaCl + ffmpeg, all present in the base image.
- **TTS**: **ElevenLabs** (`tts.provider: elevenlabs`), using `ELEVENLABS_API_KEY` from the
  environment. Free alternatives (`edge`, `neutts`) are also available in config.
- **Use it**: `/voice join` in your Discord voice channel → speak → transcribed → answered →
  spoken back. `/voice status`, `/voice leave` to manage. The bot needs voice permissions
  (Connect + Speak) and the Message Content intent.
- **Diagnose**: `docker exec hermes /opt/hermes/.venv/bin/python /opt/hermes/scripts/discord-voice-doctor.py`

---

## Camofox browser companion

The agent can drive a real, anti-detection browser — [Camoufox](https://camoufox.com), a Firefox
fork that spoofs fingerprints at the C++ level — with a human-in-the-loop VNC console for
interactive logins (so the agent can later reuse the authenticated session).

This is **not** part of this repo's `docker-compose.yaml`. It runs as its **own Coolify
application**, built from the fork [`aaka3207/camofox-browser`](https://github.com/aaka3207/camofox-browser)
(forked from [`jo-inc/camofox-browser`](https://github.com/jo-inc/camofox-browser)) via the repo's
`Dockerfile.ci` (which self-downloads the Camoufox binary at build time — unlike the default
`Dockerfile`, which needs `make fetch` first). What follows documents the companion app and how
hermes wires up to it.

> The canonical setup is this Coolify application. (An older `docs/camofox-browser.md` in this repo
> describes a standalone `make up` / host `docker run` recipe — that is **not** how it was deployed;
> prefer this section.)

### Fork changes vs upstream

1. **`camofox.config.json`** sets `vnc.enabled: true`, so the VNC apt stack (x11vnc / noVNC /
   websockify) is installed at build time.
2. **`lib/auth.js`** is patched so that when no API key is configured the server treats requests as
   trusted (LAN-only deploy). Hermes sends no auth, and without this camofox returns `403` on the
   `/tabs/:id/evaluate` endpoint for remote callers in production.

### Coolify configuration

- **Port mappings** (host): `9377:9377` (REST API) and `6080:6080` (noVNC).
- **Env**:
  - `VNC_BIND=0.0.0.0` — noVNC listens on all interfaces, not just loopback.
  - `CAMOFOX_PROFILE_DIR=/data/profiles`, `CAMOFOX_COOKIES_DIR=/data/cookies`,
    `CAMOFOX_TRACES_DIR=/data/traces` — all on a persistent volume mounted at `/data`.
  - `VNC_PASSWORD` — set (the console is password-protected).
  - `BROWSER_IDLE_TIMEOUT_MS=300000` — Camoufox idle-shuts-down after 5 min (see gotcha below).
  - `CAMOFOX_API_KEY` — **intentionally not set** (LAN-trusted; pairs with the `lib/auth.js` patch).
- **Exposure**: no public domain and no router port-forward. **LAN-only by design** — the noVNC
  console can drive a browser logged into your accounts, so it must not be internet-exposed.

### Wiring hermes to camofox

Set `CAMOFOX_URL=http://192.168.1.100:9377` (the LAN address of the camofox app) on the **hermes**
app, then add to `config.yaml` (edited **as the `hermes` user**, never root — see
[Editing config.yaml safely](#editing-configyaml-safely)):

```yaml
browser:
  camofox:
    managed_persistence: true
    user_id: hermes
    session_key: default
```

`user_id` must match the `userId` used when logging in via the VNC console, so the agent inherits
the authenticated session.

### VNC console gotcha (important)

The noVNC console at `http://192.168.1.100:6080/vnc.html` only works **while a browser session is
live**. Camoufox lazy-launches and idle-shuts-down after `BROWSER_IDLE_TIMEOUT_MS` (5 min by
default); when it's asleep, the Xvfb display and x11vnc (port 5900) don't exist, so clicking
**Connect** gives "unable to connect to server" even though the page loads.

To use it: **first start a session** (have the agent open a page, or `POST /tabs`), then connect
promptly. For long manual logins, raise `BROWSER_IDLE_TIMEOUT_MS` (e.g. `1800000`, or `0` =
never) at the cost of Firefox staying resident (~350 MB). The console is password-protected via
`VNC_PASSWORD`.

---

## Configuration

Runtime config lives on the volume, **not** in this repo:

- **`/opt/data/config.yaml`** — all agent settings (model, memory, voice, skills, …). This is
  `$HERMES_HOME/config.yaml`.
- **`/opt/data/.env`** — secrets (also injected as container env via Coolify).
- **`/opt/data/auth.json`** — OAuth tokens / credential pool (e.g. the `openai-codex` subscription
  login). Managed via `hermes auth`.

### Editing config.yaml safely

⚠️ **Always edit as the `hermes` user (UID 10000), never as root.** Because the same `~/.hermes`
dir is shared with the Syncthing container (which chowns it to `10000:10000`), a file rewritten by
root gets `root:root` ownership and the hermes process can no longer read it — you'll see
repeating `gateway.config: Failed to process config.yaml — Permission denied` warnings and the app
silently falls back to defaults (ignoring every override).

```bash
# correct: edit as hermes
docker exec -it -u hermes hermes vi /opt/data/config.yaml

# if a file ends up root-owned, fix from the host:
sudo chown 10000:10000 /path/to/.hermes/config.yaml
```

Hermes re-reads `config.yaml` on a short polling cycle, so most changes apply without a restart.

---

## Deployment (Coolify)

- **Build pack**: dockercompose, building this repo's `Dockerfile` + `docker-compose.yaml`.
- **Env vars**: all the `${...}` values in compose are set in Coolify (API keys, Discord token,
  gateway secret, etc.).
- **Post-deployment command** (runs after each deploy, in the `hermes` container):
  ```bash
  runuser -u hermes -- brv providers connect openrouter --api-key $OPENROUTER_API_KEY --model openai/gpt-oss-120b:free
  ```
  `runuser -u hermes` is required — running it as root writes brv's credentials to
  `/root/.local/share/brv` instead of the hermes user's store, where they're not seen.
- **Healthcheck**: the `hermes` service healthchecks `GET http://127.0.0.1:8642/health` (returns
  `{"status":"ok"}`). Coolify and `docker ps` report `healthy`/`unhealthy` accordingly.

### Startup ordering

`hermes` has `depends_on: { syncthing: { condition: service_healthy } }`. Syncthing chowns the
shared volume to `10000:10000` on startup; gating hermes on syncthing's healthcheck ensures the
chown finishes before hermes reads `config.yaml`, avoiding a permission race on fresh deploys.

### Operational notes

- **Build failures with `exit code 255` mid-`npm install`** are usually a transient
  buildkit/Coolify cancellation (not a Dockerfile error) — the old container keeps serving. Just
  redeploy; it typically succeeds on retry.
- **Docker healthchecks don't auto-restart unhealthy containers.** If a container goes unhealthy
  (e.g. an app process wedged after an unclean reboot), it stays up-but-unhealthy until manually
  restarted (`docker restart <name>`). Add an `autoheal` sidecar if you want self-healing.
- **After an unclean host reboot**, Coolify's own Postgres may need a minute of WAL recovery;
  `coolify` (the app) can come up before the DB is ready and sit unhealthy — a `docker restart
  coolify` clears it.

### Build-time patches against Codex (temporary)

Two `Dockerfile` patches work around problems with the ChatGPT Codex backend
(`chatgpt.com/backend-api/codex`, provider `openai-codex`). Both are idempotent and non-fatal
(if their anchor text moves, they print a warning and skip rather than break the build). They are
related but distinct: the first stops Codex from **crashing**; the second makes Mnemosyne actually
**use** Codex. Remove each when its upstream fix lands.

- **(a) openai SDK null-guard** ([#17](https://github.com/aaka3207/hermes/pull/17), merged).
  Codex began streaming events with `response.output = None`, which violates the OpenAI API
  contract and crashes the `openai` SDK's streaming parser with `TypeError: 'NoneType' object is
  not iterable` at `_parsing/_responses.py` (`for output in response.output`). This broke **every**
  Codex call — both the agent's chat loop and Mnemosyne's memory ops. It is **not** an
  auth/quota/model issue and is **not** fixable by upgrading `openai`: the offending line is
  byte-identical from the pinned `openai` 2.24.0 through 2.38.0, so it's an upstream backend
  regression. The Dockerfile patches the installed SDK to read `response.output or []`. **Remove
  this patch when NousResearch/OpenAI ship an upstream fix.**

- **(b) Mnemosyne host-LLM lazy-register** ([#18](https://github.com/aaka3207/hermes/pull/18)).
  Mnemosyne can route its memory LLM ops (fact extraction + sleep/consolidation) through Hermes'
  authenticated auxiliary client → Codex, gated by `MNEMOSYNE_HOST_LLM_ENABLED=true` (already set).
  Its provider registers that host backend in `initialize()`, but the live gateway does **not** keep
  it registered for the consolidation/extraction code paths (a module-identity / lifecycle quirk):
  `get_host_llm_backend()` returns `None` there, so memory silently falls back to a non-LLM "AAAK"
  compression encoder (lossy) for summaries and regex triples for facts — low quality. The Dockerfile
  patches `mnemosyne/core/local_llm.py` so the host-backend gates self-heal: if no backend is
  registered when an LLM call is attempted, it lazily calls `register_hermes_host_llm()`
  (idempotent) on the spot. Verified: a fresh process with no prior registration now produces clean
  LLM-extracted facts, so extraction + consolidation run on Codex (on the user's subscription, no
  per-call cost) instead of AAAK/regex. Note this patches a root-owned pip package, so the step runs
  at build time as root. **Remove this patch when Mnemosyne fixes gateway registration upstream.**

---

## Common operations

```bash
# shell into the running container as the hermes user
docker exec -it -u hermes hermes bash

# tail logs
docker logs -f hermes

# health
curl -fsS https://hermes-api.aakashe.org/health      # {"status":"ok",...}
docker inspect --format '{{.State.Health.Status}}' hermes

# memory
docker exec -u hermes hermes hermes memory status
docker exec -u hermes hermes hermes mnemosyne stats
```

Container names on the Coolify host are suffixed (e.g. `hermes-tgg4k0sc8wgocck08cc4s4cg-…`); use
`docker ps --format '{{.Names}}' | grep '^hermes-'` to resolve the current one.

---

## Repo contents

| Path | Purpose |
|------|---------|
| `Dockerfile` | The custom image (upstream + the additions above) |
| `docker-compose.yaml` | hermes + syncthing services, volume, healthchecks, env |
| `hub/` | Hermes hub content (SOP, changelog, notion link) |
| `skills/` | Custom agent skills |

## License

The upstream Hermes Agent is governed by its own license. This repo only contains deployment
configuration and custom content.

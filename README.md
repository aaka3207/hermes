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

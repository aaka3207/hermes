# Hermes base-image update and the PM rework, 2026-10-09

Record of the session in which the Coolify rebuild of `hermes` was failing, the cause, the fixes,
what was verified, and what is still open. Everything here was observed on 2026-10-09 unless a
statement is marked as an inference.

## Summary

- The Coolify rebuild failed because `FROM nousresearch/hermes-agent:latest` now points at upstream's
  unified package manager ("PM") rework (v0.21.x, Python 3.14.7, Debian 13). `uv` is no longer on
  `PATH` in that image, so our first `RUN ... uv pip install` exited 127.
- Two Dockerfile workarounds were committed and deployed (`4981635ff`).
- After the deploy, OpenViking stopped loading. Upstream moved the provider out of core, and PM could
  not auto-install it because of an upstream PM bug that breaks every plugin install when any
  installed plugin has a tooling-only `pyproject.toml` (`hermes-lcm` is one). A workaround was
  applied **on the production volume only** and OpenViking was reinstalled as a catalog plugin.
- Follow-up the same day (second commit): the base is now **pinned by digest**, npm globals install
  to `/usr/local` (which cleared the `hermes pm doctor` node digest mismatch, confirmed), and
  `claude` got an explicit postinstall because npm 12 runs no install scripts by default.
- After the redeploy, MCP sign-in failed with "streamable_http is not available". Cause: the plugin
  install built a PM generation without the image's baked extras (upstream #135329 / `fe2b130b`).
  Fixed with `hermes pm install --extra ...`. A second error, `invalid_redirect_uri`, came from a
  missing `dashboard.public_url` behind the TLS proxy; fixed by setting it. Akiflow now works. See
  "Post-deploy: MCP sign-in failure".
- Open: a malformed `lcm.db` for the
  `dinefile` profile (pre-existing), and a MetaMCP 524 seen once.

## Timeline

1. **Failure.** Deploys on Oct 7, Oct 8 and Oct 9 (15:44 UTC) failed. The running container was the
   Oct 1 build (`healthy`, serving stale code). Coolify reported
   `docker compose build ... exit code 1`, with the failing step at `Dockerfile:49`
   (`apt-get ... npm install -g ... uv pip install ...`), `exit code: 127`.
2. **Diagnosis.** Ran the current `latest` image on the Coolify server and checked for tools:
   `apt-get` and `npm` present, `uv` missing from `PATH`. `uv` 0.12.3 exists at
   `/opt/hermes/tools/uv-0.12.3-linux-x64/uv`. The venv has no `pip` or `uv` module.
3. **Why upstream did it.** Read the Dockerfile shipped inside the image (`/opt/hermes/Dockerfile`)
   and the upstream PR. See "Upstream context".
4. **Test build.** Built our Dockerfile on the Coolify server (amd64, same as production) with
   throwaway tags, no deploy. Build 1 passed with only the `uv` fix. The smoke test then found that
   `claude`, `ntn`, `vercel`, `supabase` and others were missing from `PATH`. Added the symlink loop,
   rebuilt (build 2), and the smoke test passed. Test images and temp files were removed afterwards.
5. **Commit and deploy.** Committed `4981635ff` to `main` and pushed. The user triggered the Coolify
   deployment `ia06l18y6tfc7dwcc2fzcadk` on that commit.
6. **OpenViking error after restart.** See "OpenViking".
7. **Upstream issue search.** Found the matching upstream bug and other PM-related issues. See below.

## Root cause 1: the base image changed

`FROM nousresearch/hermes-agent:latest` plus Coolify's `--pull` build moved us onto upstream's PM
rework (PR NousResearch/hermes-agent#102765, 1,324 commits, closed 2026-09-24):

| | Oct 1 build (worked) | New `latest` (v0.21.6) |
|---|---|---|
| `uv` | `/usr/local/bin/uv` | `/opt/hermes/tools/uv-0.12.3-linux-x64/uv`, not on `PATH` |
| Python | 3.13.5 | 3.14.7 |
| `/opt/hermes/pm` | absent | present |
| npm global prefix | `/usr/local` | `/opt/hermes/tools/node-26.7.0-linux-x64` (bin not on `PATH`) |

## Upstream context

- The image's own Dockerfile comments say PM is now the single authority for the toolchain:
  `pm/lock.json` pins uv, Node, npm, Python, ffmpeg and ripgrep with SHA-256 hashes. It replaced an
  Astral uv image tag that had drifted (0.11.6 vs the locked 0.12.3), which they call "the
  two-authorities failure".
- They symlink `python3`, `node`, `npm`, `ffmpeg`, `rg` into `/usr/local/bin` but not `uv`. Their
  comment: "build consumers receive Python environments, never an installer executable." Reading
  that as intentional is an inference from the comment; no changelog entry was found.
- The PR describes the image venv as sealed: `/opt/hermes/.venv` "is never written" at runtime, and
  opt-in extras go to PM generations under `/opt/data/installs`. The PR's contributor table says
  `uv pip install X` into the venv is "Not permitted" for development.
- Tested on the server: at build time (root) the venv is writable and
  `uv pip install --python /opt/hermes/.venv/bin/python sqlite-vec==0.1.9` with the in-image uv
  succeeded. So build-time installs are not blocked.

## Changes made

### In the repo (commit `4981635ff`, pushed to `main`)

`Dockerfile`:

1. `COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv` before the first `RUN`.
   The pin matches the base's uv today and will not follow it when upstream bumps.
2. After `npm install -g ...`, a loop that symlinks everything in `"$(npm prefix -g)"/bin/` into
   `/usr/local/bin`, skipping names that already exist (`node`, `npm`, `npx`).

### In the repo, follow-up commit (same day)

1. `FROM nousresearch/hermes-agent@sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784`.
   This is the image `latest`, `stable` and `rc.4-v0.21.6` all point to on Docker Hub (published
   2026-10-08 12:08 UTC) and the one we tested. **The plain `v0.21.6` tag is a different, earlier
   build (`sha256:55e192fb...`)**, so pinning to that tag would not have matched what was tested.
2. `npm install -g --prefix /usr/local ...`, replacing the symlink loop. With the default prefix, the
   packages landed in PM's hash-verified node tree and `hermes pm doctor` reported
   `✗ node: realized bytes do not match recorded digest`. In the pinned test image the doctor shows
   `✓ node 26.7.0`, `✓ npm 12.0.2`, `✓ python`, `✓ uv`, and PM's node `bin/` holds only
   `node`, `npm`, `npx`.
3. `node /usr/local/lib/node_modules/@anthropic-ai/claude-code/install.cjs` after the install.
   npm 12 has an empty `allow-scripts` list, so claude-code's postinstall never ran and
   `claude --version` failed with "native binary not installed" (also true in the first deployed
   build, `4981635ff`; the package's native binary itself works, 2.1.295). The other global CLIs
   (`ntn`, `vercel`, `supabase`, `upstash`, `posthog-cli`, `brv`) work without it.

Both builds were tested on the Coolify server before committing; see Verification.

### On the production volume only (not in the repo)

- Appended to `pyproject.toml` in **both** copies of `hermes-lcm`:
  - `/opt/data/plugins/hermes-lcm/pyproject.toml`
  - `/opt/data/profiles/dinefile/plugins/hermes-lcm/pyproject.toml`

  ```toml
  [project]
  name = "hermes-lcm"
  version = "0.1.0"
  requires-python = ">=3.11"
  ```

  Originals are backed up as
  `/opt/data/backups/pyproject.toml.hermes-lcm.20261009` and
  `/opt/data/backups/pyproject.toml.hermes-lcm-dinefile.20261009`. Both plugin directories are git
  checkouts, so `git checkout pyproject.toml` also reverts the edit.
- `hermes plugins install openviking --yes-deps --enable` (inside the `hermes` container as user
  `hermes`). It installed `httpx`, `psutil`, `packaging` and the plugin to
  `/opt/data/plugins/openviking`.

### On the Coolify server (cleaned up)

Uploaded a build context to `/tmp/hermes-testbuild`, built `hermes-testbuild:uvfix` and `:uvfix2`
(about 8 GB each), then removed the images, one leftover never-started container, and the temp
files. A local tarball was also deleted.

## OpenViking

After the deploy, the container log showed:

```
Memory provider 'openviking' moved out of core and could not be installed automatically:
Plugin 'openviking' was not published: venv: uv lock exited 2: error: Failed to parse:
`plugin-sources/hermes-lcm-c2c24a3f9d20a2ac/pyproject.toml` ... `[project]` ... the required
`project.version` field is neither set nor present in the `project.dynamic` list
```

- `config.yaml` has `memory.provider: openviking`, but the new image bundles only `byterover`,
  `holographic`, `mnemosyne`, `retaindb`. There was no `openviking` Python module.
- PM resolves all installed plugins, across all profiles, as one uv workspace. For a plugin whose
  `pyproject.toml` has no `[project]` table, PM injects `[project].name` but no `version`, and
  `uv lock` rejects it. `hermes-lcm`'s `pyproject.toml` is ruff configuration only. Upstream
  `hermes-lcm` `main` (`8d1b1e6`, v1.0.0-rc.1, 2026-09-03) is identical to the installed copy, so
  updating the plugin does not help.
- The error kept reporting the same `c2c24a3f9d20a2ac` hash after patching the first copy. Patching
  the `dinefile` profile copy as well cleared it. (`find -xdev` initially hid the second copy
  because `/opt/data` is a separate mount.)
- After the install, `hermes memory status` reported provider `openviking`, `Status: available`,
  with the existing config preserved: `use_ovcli_config: True`,
  `ovcli_config_path: /opt/data/.openviking/ovcli.conf.owner`, endpoint `https://ov.aakashe.org`.
  `hermes memory setup openviking` was **not** run (its Quick Local path installs a local server).
- Only the status check was done. No recall or write was exercised. Sessions already running will
  not pick up the provider until they restart.

## Verification

- Server test build: Python 3.14.7. Imports OK in the image venv: `mnemosyne`, `sqlite_vec`,
  `hindsight_client`, `faster_whisper`, `cognee`. The `hermes_agent.memory_providers` entry point
  lists `cognee`. The mnemosyne link and `plugin.yaml` build checks passed.
- `PATH` resolution in build 2: `claude`, `ntn`, `vercel`, `supabase`, `upstash`, `posthog-cli`,
  `brv`, `syncthing`, `uv`, `node`, `npm`, `npx`, `hermes`.
- Production deploy `ia06l18y6tfc7dwcc2fzcadk` came up healthy: containers `hermes`, `hermes-webui`,
  `mnemosyne-dashboard`, `syncthing` all `healthy`.
- `/opt/data/lcm.db` (390 MB): `PRAGMA quick_check` returned `ok` (SQLite 3.53.1, read-only open).

### Second round (pinned image, test build on the server)

- Python imports (`mnemosyne`, `sqlite_vec`, `hindsight_client`, `faster_whisper`, `cognee`) OK.
- `claude 2.1.295`, `ntn 0.23.19`, `vercel 63.1.0`, `supabase 2.120.0`, `upstash 1.5.0`,
  `posthog-cli 0.18.10`, `brv 3.16.1` all run.
- `hermes pm doctor`: `✓ node 26.7.0`, `✓ npm 12.0.2`, `✓ python 3.14.7+20260901`, `✓ uv 0.12.3`, no `✗`.
- Test images, a leftover container and temp files were removed afterwards.
- A one-shot agent turn in the **running** container (session `20261009_113527_e6892f`, prompt
  labelled `[deploy-check 2026-10-09, safe to ignore]`) answered `OK`, and the plugin logged
  `OpenViking session ... committed on session end (1 turns)`. That confirms the provider's write
  path accepted the turn. It was not searchable in OpenViking afterwards, which is plausible for a
  trivial turn but not proven. Recall through Hermes's prefetch was **not** verified separately;
  the OpenViking server's own search and health were fine.
- Upstream's build path after the pinned image, as checked at about 16:30 UTC: `Dockerfile` had
  no commits since Sept 28 beyond the one already included; `docker/` and `scripts/bundles/`
  changes since were a separate sandbox image, desktop packaging and lint. **This became stale
  the same day**: upstream commit `fe2b130b` (authored 17:12 UTC, committed 17:35 UTC) changed the
  Dockerfile. See "Post-deploy: MCP sign-in failure" below.

## Post-deploy: MCP sign-in failure (resolved in two steps, see "Resolution")

After the redeploy of `f62dd1080` (16:49 to 16:55 UTC), the user could not sign in to MCP servers
in Hermes:

```
Could not sign in to the MCP server: MCP server 'akiflow' requires HTTP transport but
mcp.client.streamable_http is not available. Upgrade the mcp package to get HTTP support.
```

**Cause (hypothesis, then confirmed by the `pm status` receipt below):** upstream bug #135329, fixed in upstream commit `fe2b130b`
("fix(pm): record the Docker image's baked extras and restore them on boot", committed
2026-10-09 17:35 UTC, after our pinned image). Its message: the image bakes eight extras into
`/opt/hermes/.venv` but never recorded them as PM's dependency selection, so the first generation a
container builds (a lazy extra, or **a plugin's dependencies**) starts from an empty selection and
"dropped aiohttp, the messaging SDKs and mcp. After the next restart the API server, platform
adapters and MCP servers failed to start." The upstream Dockerfile now builds the venv with
`--record-selection`, and stage2 restores missing baked extras on boot. Our pinned image
(Oct 8) has neither.

**Probable trigger: our own action.** `hermes plugins install openviking --yes-deps --enable`
(about 11:30 local) built a new generation under `/opt/data/installs` with the plugin's three
dependencies (`httpx`, `psutil`, `packaging`). The 16:49 redeploy restarted the containers, which
booted from that generation. Supporting detail: at 11:23, before the plugin install, the log still
showed an MCP client request (`httpx2: GET https://metamcp.aakashe.org/metamcp/hermes/mcp`, which
returned 524), so the MCP SDK imported then.

At the time of writing the server was unreachable (the user was on another network; the SSH
profile uses the LAN address), so this was first diagnosed from upstream and confirmed later.

**Related upstream issues checked:**
- [#123770](https://github.com/NousResearch/hermes-agent/issues/123770): the `mcp` extra is dropped
  when PM migrates a legacy venv, with the same error. Workaround stated there:
  `hermes pm install --extra mcp`. Same bug class, different trigger.
- [#122395](https://github.com/NousResearch/hermes-agent/issues/122395): a process activating a
  dependency generation built for another interpreter ABI fails the same way. Reported for source
  installs with an in-tree venv, so it probably does not apply here.
- [#134107](https://github.com/NousResearch/hermes-agent/issues/134107): the `solstice ... No module
  named 'httpx'` warning is emitted by PM's deliberately minimal worker runtime, **not** by the main
  or dashboard process. It does not imply the dashboard lacks `httpx`; an earlier suggestion in this
  session that the two were linked was wrong.
- A separate, older case in the maintainer's notes: a single process can cache a failed `import mcp`
  for its lifetime (`tools/mcp_tool.py::_ensure_mcp_sdk`), fixed by restarting that process.

**Plan (executed, see Resolution):**
1. Read-only: `hermes pm status`, the recorded selection in `facts.json`, and
   `grep -i -E "mcp|parked|streamable" /opt/data/logs/agent.log | tail -60`.
2. If the selection lacks the image's extras, restore them with an explicit sync
   (`hermes pm install --extra ...` for the image's baked extras: `all`, `messaging`, `otlp`,
   `anthropic`, `bedrock`; check the exact flag shape with `hermes pm install --help` first), or
   deselect the generation so the image's own venv boots. Then restart the dashboard and gateway and
   check `hermes memory status`, the MCP page and Discord.
3. When a published image contains `fe2b130b` (the Docker Hub rc and `main` tags seen so far predate
   it), bump the pinned digest after a test build; its stage2 refresh restores the baseline
   automatically.

**Lesson:** on this image, installing a plugin with `--yes-deps` may drop the baked extras on the
next restart until the upstream fix is in the image.

### Resolution

**Step 1, the import error.** `hermes pm status` showed the latest sync receipt with
`feature_list: audio-io, discord, wake-openwakeword` (sync 18:21 UTC): the baked extras were not in the
recorded selection, which confirmed the #135329 mechanism. The user ran

```
hermes pm install --extra all --extra messaging --extra otlp --extra anthropic --extra bedrock
```

The sync succeeded at 19:38 UTC, the receipt's feature_list then included `all`, `anthropic`,
`bedrock`, `messaging`, `otlp`, and an `mcp http` import check passed. The "streamable_http is not
available" error went away. Discord had kept answering throughout.

**Step 2, `invalid_redirect_uri`.** The next sign-in attempt failed one layer up, at OAuth dynamic
client registration:

```
Registration failed: 400 {"error":"invalid_redirect_uri", ... "Redirect URI is not allowed:
http://hermes-dashboard.aakashe.org/api/mcp/oauth/callback/akiflow"}
```

Only the OAuth MCP servers failed; header/bearer-key servers (MetaMCP and so on) never build a
redirect URI. Upstream builds it in `hermes_cli/web_routers/mcp.py::_mcp_oauth_callback_url`: it uses
`HERMES_DASHBOARD_PUBLIC_URL` (env) or `dashboard.public_url` (config.yaml) verbatim when set, and
otherwise reconstructs the URL from `request.base_url`. Neither was set, and behind Traefik (TLS
terminated) the dashboard sees plain http, hence the `http://` URI that Akiflow rejected.

Fix, both applied:
- `dashboard.public_url: https://hermes-dashboard.aakashe.org` in `/opt/data/config.yaml` (set by
  Hermes; verified on the server at line 102). Upstream re-reads config per request, so no restart
  was needed for it.
- `HERMES_DASHBOARD_PUBLIC_URL=https://hermes-dashboard.aakashe.org` added as a runtime-only env var
  on the Coolify app (uuid `x5m8razwsrpsaiasapudfabm`). It takes effect on the next redeploy and
  takes precedence over the config key; both hold the same value.

**Verified:** the user signed in to Akiflow from the dashboard, restarted the gateway, and Hermes
reported it could retrieve 11 calendars and 6 inbox tasks. Other OAuth servers were not individually
re-tested in this session.

The `public_url` setting is a first-time requirement, not a regression from the image update: nothing
in the repo or the Coolify env set it before. Whether the dashboard OAuth flow worked earlier by some
other route was not checked.

## Safety copy taken before the follow-up

`/opt/data/backups/pre-pin-20261009/` (mode 700, files 600): `state.tgz` (config.yaml, plugins,
the dinefile profile's plugins and config.yaml, `.openviking`) and `lcm.db` (a consistent SQLite
backup of the main database, `quick_check` ok). `installs/` was skipped; PM regenerates it.

## Open items

1. ~~`hermes pm doctor` node digest mismatch~~ Resolved in the follow-up commit (see above), pending
   a production redeploy of that commit.
2. **`/opt/data/profiles/dinefile/lcm.db` (52 MB) is malformed** (`quick_check` raises
   `database disk image is malformed`). Not modified since Sep 17, so likely pre-existing. It matches
   the boot warning `Failed to load plugin 'hermes-lcm': database disk image is malformed`.
   Untouched. `sqlite3 .recover` is a possible recovery.
3. **MetaMCP 524** on `https://metamcp.aakashe.org/metamcp/hermes/mcp` appeared once in the log.
   OpenViking is reached through MetaMCP. Not investigated.
4. **The `hermes-lcm` edit is fragile.** `hermes plugins update hermes-lcm` or a reinstall may
   conflict with or drop it until upstream fixes the PM bug.
5. ~~Pin the base.~~ Done in the follow-up commit (digest). Bump deliberately: after `v0.21.7` leaves
   rc (rc.3 to rc.5 were published on Oct 8 and 9), test-build it as above, then change the digest.
   Which tag the Oct 1 build used is still unconfirmed.
6. **Unchecked after the move to PM:** `hermes-webui` (my notes say it regenerates `/app` from
   `/apptoo`), the first boot behaviour of `/opt/data/installs`, and ~~OpenViking recall through
   Hermes's prefetch~~ (verified 2026-10-09 evening: Hermes quoted back an injected
   `viking://user/owner/memories/events/2026/10/09/write_map_agreed.md` and the server entity record,
   so write and read-back both work; this is Hermes's own report of its context). Also seen in the test
   turn: `Auxiliary title generation failed: OpenAI Responses request is missing input` and
   `Unknown toolsets: homeassistant`, neither investigated.
7. **Benign warnings seen:** `Failed to load bundled provider plugin solstice: No module named 'httpx'`
   (upstream #134107, #134220, #134127), plus `homeassistant` plugin and `wake.start(gui)` failures
   that came from the same `uv lock` error and should be re-checked after the fix.

## Upstream issues referenced

- [#129632](https://github.com/NousResearch/hermes-agent/issues/129632): PM injects `[project].name`
  without `version` for tooling-only plugin `pyproject.toml`. This is the OpenViking blocker.
- [hermes-lcm#637](https://github.com/stephenschoettler/hermes-lcm/issues/637): same error, with the
  reporter's workaround (the block we applied). Open since 2026-09-27.
- [#135589](https://github.com/NousResearch/hermes-agent/issues/135589): on Python 3.14 the 14-day
  `exclude-newer` quarantine breaks the plugin-workspace `uv lock` for exact-pinned packages
  ("has no publish time"), blocking openviking/homeassistant/solstice installs. It did not block us
  today but could on a future update.
- [#129097](https://github.com/NousResearch/hermes-agent/issues/129097): `pip install` writing into a
  hash-verified store. Same class as open item 1 but on the Python side.
- [#102765](https://github.com/NousResearch/hermes-agent/pull/102765): the PM rework itself.

## How to revert

- Dockerfile: `git revert 4981635ff`. Note the old Dockerfile will not build on the current
  `latest`.
- Volume: restore the two `pyproject.toml` backups listed above, or run `git checkout pyproject.toml`
  inside each plugin directory. OpenViking would then stop loading again.
- Image: Coolify can redeploy the Oct 1 image. Not tested against a volume the new PM has already
  written to.

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
- Open: a malformed `lcm.db` for the `dinefile` profile (pre-existing), and a MetaMCP 524 seen once.

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
- Upstream's build path after the pinned image: `Dockerfile` has no commits since Sept 28 beyond
  the one already included; `docker/` and `scripts/bundles/` changes since are a separate sandbox
  image, desktop packaging and lint. Nothing that affects this image was found.

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
   `/apptoo`), the first boot behaviour of `/opt/data/installs`, and OpenViking recall through
   Hermes's prefetch (the write path was seen to commit; see Verification). Also seen in the test
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

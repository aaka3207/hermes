# RTK command rewriting

[RTK](https://github.com/rtk-ai/rtk) ("Rust Token Killer") rewrites the agent's shell commands into
compact equivalents before they run, so each `terminal` tool call returns far fewer bytes for the
model to read:

```
cat README.md        ->  rtk read README.md
git status           ->  rtk git status
grep -rn foo src/    ->  rtk grep -rn foo src/
cd /repo && git status -> cd /repo && rtk git status
```

The agent's workflow doesn't change — it keeps writing ordinary shell commands, and the rewrite
happens underneath it.

## How it's wired here

Two pieces, both baked into the image by the `Dockerfile` (see the `rtk` block near the end):

| Piece | Path | Installed by |
|-------|------|--------------|
| The Rust binary | `/usr/local/bin/rtk` (pinned release + SHA-256) | `docker/rtk-install.sh` |
| The Hermes adapter plugin | `/opt/hermes/plugins/rtk-rewrite/` | same script, via `rtk init --agent hermes` |

The plugin is a thin Python adapter: it registers a `pre_tool_call` hook, and for `terminal` tool
calls shells out to `rtk rewrite <command>` and replaces the tool's `command` argument with whatever
comes back. Every rewrite decision lives in the binary, so bumping `rtk` changes the behavior
without touching the adapter.

**Why the image and not the volume.** Upstream's documented install is `rtk init --agent hermes`,
which writes into `$HERMES_HOME` — `/opt/data` here, i.e. the runtime volume the build can't see,
with a separate copy per profile. The install script runs that same installer against a throwaway
home and relocates only the plugin into the image's bundled plugin dir, which
`hermes_cli/plugins.py` scans ahead of the user dir. One copy serves both the default and `dinefile`
gateways, nothing mutates the volume at boot, and the adapter can never drift from the binary it
calls.

## Enabling it (one-time, per profile)

Discovery finds the plugin, but `plugins.enabled` is an opt-in allow-list and bundled auto-load
applies only to `backend`/`platform` plugins — this one is `standalone`, so it stays dormant until
you enable it. `config.yaml` lives on the volume, so this survives redeploys and only needs doing
once per profile:

```bash
# default profile
docker exec -it -u hermes hermes hermes plugins enable rtk-rewrite

# dinefile profile (separate HERMES_HOME, separate config.yaml)
docker exec -it -u hermes hermes hermes --profile dinefile plugins enable rtk-rewrite
```

Equivalent by hand — in `/opt/data/config.yaml` (and `/opt/data/profiles/dinefile/config.yaml`),
**edited as the `hermes` user**, never root (see "Editing config.yaml safely" in the README):

```yaml
plugins:
  enabled:
    - rtk-rewrite
```

Restart the gateway afterwards: plugin registration happens at startup, unlike most config keys.

## Verifying

```bash
# 1. the binary is on the agent's PATH and is the right `rtk`
docker exec hermes rtk --version
docker exec hermes rtk rewrite 'cat README.md'     # -> rtk read README.md

# 2. Hermes sees the plugin and considers it enabled
docker exec -u hermes hermes hermes plugins list | grep rtk-rewrite

# 3. it is actually saving anything
docker exec -u hermes -e HOME=/opt/data hermes rtk gain
```

If `rtk gain` shows nothing after the agent has run commands, the hook isn't firing — check that the
plugin is enabled *for the profile that gateway runs*, and that the gateway was restarted.

## Failure behavior

The integration is fail-open by design, at every layer. The command runs unchanged (never blocked)
when:

- `rtk` is missing from the agent process's PATH — the hook isn't even registered, and one warning
  goes to stderr;
- `rtk rewrite` errors or takes longer than 2s;
- the tool isn't `terminal`, or its payload has no string `command`;
- the adapter raises for any other reason.

RTK also declines to rewrite some commands by design: ones already prefixed with `rtk`, heredocs,
and anything with no matching filter. Those pass through untouched.

The build fails loudly, though, if any of this is broken at image-build time — `docker/rtk-smoke.py`
checks the binary is on PATH and really rewrites, that Hermes' own `scan_directory`/`gate_manifest`
accept the plugin, and that the adapter mutates a terminal payload in place while passing every
other shape through. Three of those four failures are invisible in production (a healthy agent that
silently stopped compressing), which is why they're asserted at build time.
`tests/test_rtk_smoke.py` covers the guard itself and runs on plain stdlib anywhere.

## Upgrading rtk

1. Read the new release's `checksums.txt`.
2. In `docker/rtk-install.sh`, bump `RTK_VERSION` **and** both `SHA_*` values together.
3. Rebuild. The smoke check re-verifies the adapter/binary contract end to end, since the plugin is
   regenerated from the new binary during the build.

## Notes

- **Telemetry is off.** RTK's usage ping needs explicit consent (unset by default), and the released
  builds compile in no collector URL. `RTK_TELEMETRY_DISABLED=1` is set in the image anyway.
- **Runtime state** (`rtk gain` history, config) lives under `$HOME/.config/rtk` — `HOME` is
  `/opt/data` for the gateway, so it lands on the volume and survives redeploys.
- **Cost per call** is one short-lived subprocess with a 2s timeout, on tool calls only — not on the
  token path.

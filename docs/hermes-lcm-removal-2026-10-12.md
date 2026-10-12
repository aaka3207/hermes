# Removing hermes-lcm from the default Hermes profile (2026-10-12)

Decision (user, 2026-10-12): cut LCM; it causes more problems than it solves and is outdated.

**Status: executed 2026-10-12, steps 1 to 3 and 5.** Order and result:

| Step | Result |
|---|---|
| Step 3 first: SOUL.md and three skills (`hermes-compaction-diagnostics`, `home-assistant-control`, `hevy-workout-operations`) repointed to `session_search`, through `skill_manage` | Ledger ids `7e1172550361`, `f3e3c7974b4d`, `ad4cfe4480d3`; SOUL.md backup `SOUL.md.bak-lcm-removal-20261012`; curator snapshot `2026-10-12T14-11-30Z` |
| Step 1: `context.engine: compressor` (user), `hermes plugins disable hermes-lcm` | `plugins.disabled: [hermes-lcm]`; the disable ran a PM sync, extras unchanged (`pm status` ok) |
| Restart | Both s6 services restarted in place; image digest unchanged (`sha256:8bfe6b5d...`). Not a Coolify redeploy, to avoid pulling a Hermes update |
| Step 5 (done early, at the user's request) | `lcm.db*` (429 MB) and `plugins/hermes-lcm` moved to `/opt/data/backups/lcm-removal-20261012/`, with `lcm.db.sha256` |
| Not done | A long session compacting on the built-in compressor has not been observed yet |

Side effects: a `hermes curator ledger --compact` run during verification rewrote the ledger (541 entries, 35 unreferenced blobs dropped; ids and rollback preserved).
`dinefile`, the image guard and the three paused cron prompts that mention `lcm_inspect` are unchanged. The plan as written follows.

Original status line: production writes are gated: you or Hermes run them. Scope is the **default** profile. The `dinefile` profile has its own copy of the plugin and its own `lcm.db`; it is left alone.

## What I verified (read-only, 2026-10-12)

| Item | Finding |
|---|---|
| How LCM is wired | `config.yaml` line 115 `context.engine: lcm`; line 270 `plugins.enabled: hermes-lcm`. It is the **context engine** (live compaction), not just a search tool. |
| Fallback | `agent/agent_init.py:_select_context_engine`: `context.engine` unset or `compressor` returns the built-in ContextCompressor. Our `compression:` block (threshold 0.5, target_ratio 0.2, protect_first 3 / last 20) already configures it. |
| Recent use | `agent.log` has 5 LCM-tool hits, all failures: four `'lcm_grep' is not a known tool name` and one terminal call querying `lcm.db` with raw SQLite. |
| Native replacement for history lookup | Hermes ships `session_search` (`/opt/hermes/tools/session_search_tool.py`), independent of LCM. I did not test a call. |
| Data | `/opt/data/lcm.db` 429 MB (+ WAL/SHM). Not mirrored anywhere; OpenViking holds extracted memories, not transcripts. |
| Known breakage | upstream hermes-lcm#588 (SQLite POSIX-lock drop) is why the image carries `docker/lcm-588-mitigation.py` and an entrypoint guard. The prod-volume `pyproject.toml` edits from 2026-10-09 were also for this plugin. |

## Step 1: switch engine and disable the plugin (reversible)

```bash
C=hermes-tgg4k0sc8wgocck08cc4s4cg-193931467615   # re-resolve with docker ps
docker exec $C cp /opt/data/config.yaml /opt/data/config.yaml.bak-20261012-pre-lcm-removal
docker exec $C hermes config set context.engine compressor
docker exec $C hermes plugins --help          # find the disable verb; the next line assumes it exists
docker exec $C hermes plugins disable hermes-lcm
docker exec $C grep -n -E "engine:|hermes-lcm" /opt/data/config.yaml
```

If there is no `plugins disable`, remove the `- hermes-lcm` line under `plugins.enabled` (line 270) with `hermes config` or a hand edit.

Restart both processes (they hold their own config; see `hermes/runtime-topology-and-mcp-reload`):

```bash
docker exec $C /command/s6-svc -r /run/service/gateway-default
docker exec $C /command/s6-svc -r /run/service/dashboard
```

Do not restart mid-session, and not while the 12:00 crons are running.

## Step 2: verify

1. `agent.log` after restart: no `Context engine 'lcm' not found` warning and no `hermes-lcm` load lines.
2. Start a session and check it runs; a long conversation should still compact (threshold 0.5).
3. Ask Hermes to find something from last week; expect `session_search`, not `lcm_*`.
4. `grep -c -i lcm /opt/data/logs/agent.log` stops growing.

## Step 3: SOUL.md (four lines, plus the tier-2 row)

| Line | Now | Change |
|---|---|---|
| 15 | "...or Hermes-LCM session history, so..." | "...or session history, so..." |
| 21 | tier 2 row "Hermes-LCM session history" | "Hermes session history (`session_search`)" |
| 33 | "use Hermes-LCM" | "use `session_search`" |
| 54 | "verify the source session ID with Hermes-LCM if available" | "verify the source session ID with `session_search`" |

If the four-tier framing is dropped (see the earlier discussion), fold these into that rewrite instead.

## Step 4: skills that mention LCM (read before editing)

`autonomous-ai-agents/hermes-compaction-diagnostics/SKILL.md` (probably LCM-specific, likely archive),
`productivity/home-assistant-control/references/home-intents.md`, `health/hevy-workout-operations/SKILL.md`.
`cron/jobs.json` has 3 `lcm` matches; I did not check which jobs. Probably the paused cognee ones.

## Step 5: delete, after a week of clean running

```bash
docker exec $C sh -c 'mkdir -p /opt/data/backups/lcm-removal-20261012 && mv /opt/data/lcm.db* /opt/data/plugins/hermes-lcm /opt/data/backups/lcm-removal-20261012/'
```

`mv`, not `rm`: the db is the only copy of about 21k messages. Delete the backup directory once you are sure.

## Not touched here

- `docker/lcm-588-mitigation.py`, `docker/entrypoint-lcm-guard.sh`, the Dockerfile block at lines 798-838, `tests/test_lcm_588_mitigation.py`. `dinefile` still runs LCM, so the guard stays until that profile is decided.
- `/opt/data/profiles/dinefile/` (plugin copy, `lcm.db`, `engine: lcm`). Its db is already reported malformed.
- The 2026-10-09 `pyproject.toml` edits in both plugin dirs.

## Rollback

`hermes config set context.engine lcm`, re-enable the plugin, restart both services. If step 5 was done, move the files back from `backups/lcm-removal-20261012/`.

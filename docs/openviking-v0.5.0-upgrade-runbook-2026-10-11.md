# OpenViking v0.4.23 → v0.5.0 upgrade runbook (2026-10-11)

Status: **executed 2026-10-11 20:30 UTC (user bumped the image); basic post-checks passed, items 3, 4, 7 of section 5 still pending.** Production writes and deploys are gated: you or Hermes run the commands. Release notes:
https://github.com/volcengine/OpenViking/releases/tag/v0.5.0 (published 2026-10-09).

## 0. What was verified today, and what was not

| Item | State |
|---|---|
| Coolify service `waw5ghezsp84k9ytyqcnsqi1` | `running:healthy`, image `ghcr.io/volcengine/openviking:v0.4.23` (read from Coolify) |
| Image tag `v0.5.0` on ghcr | manifest exists (HTTP 200) |
| Owner policy `user_config.json` | four `memory_types`, peers + self on, `add_targets: {}`, no `working_memory` key |
| Queue depth, `ov.conf` contents, volume name | **not checked** (ssh-mcp is on the LAN IP and was disconnected) |
| Schema or data migration notes in v0.5.0 | none listed. Not proof that none runs on boot, hence the snapshot |

## 1. What the release changes for us

| Change | Effect here | Action |
|---|---|---|
| Working Memory default `true` → `false` (#5696) | **On today** (v0.4.23 default; 10-10 archives hold `# Working Memory` in `.overview.md`). Our stored policy has no `working_memory` key, so after the upgrade it resolves to `false`: commits stop generating archive overviews and checkpoint summaries. Extraction is unaffected | Decide in section 5 whether to set it explicitly |
| Queued tasks keep old semantics | A task queued before the upgrade can still produce summaries | Drain the queue first (section 2) |
| Context-mode recall drops preset dirs, refills quotas (#5679); `exclude_uris` on a directory now excludes the subtree (#5627) | Results for the same query change | Re-run the benchmark (section 5) |
| MCP `remember` returns "submitted for extraction" + `task_id` (#5678) | Text differs for Claude Desktop | Nothing parses the old string that we know of |
| Embedding HTTP 400 no longer opens the account breaker (#5822) | One bad request stops killing recall | None |
| Gateway (Beta) | Off unless `gateway.enabled: true` in `ov.conf` | Leave off |
| Hermes plugin fixes (#5641, #5643, #5706, #5736) | Ship in the plugin, not the server | Separate step; see section 6 |
| Peer routing | **Not addressed.** Owner turns and `viking_remember` still land at the user root | Still open |

## 2. Pre-flight (read-only)

Run on the server (`ssh ameer@192.168.1.100`, LAN only). Container name from earlier notes: `openviking-waw5ghezsp84k9ytyqcnsqi1`.

```bash
C=openviking-waw5ghezsp84k9ytyqcnsqi1
docker ps --filter name=$C --format '{{.Names}}  {{.Image}}  {{.Status}}'
docker volume ls | grep -i ov-data            # record the exact volume name for section 3
docker exec $C ls -la /app/.openviking | head -30
docker exec $C python -c "import openviking,sys;print(openviking.__version__)" 2>/dev/null
```

Queue must be idle. Use Studio (Observer) or the observer endpoint; I did not verify the exact route, so confirm in Studio first:

```bash
# unverified route, adjust if Studio shows a different one
curl -s -H "X-API-Key: $OV_ADMIN_KEY" https://ov.aakashe.org/api/v1/observer/queue
```

Also confirm:
- No import is running (never restart mid-import, per the 10-08 session).
- Nobody is mid-session in Hermes or Desktop. A commit at upgrade time is the main risk.
- Current codex VLM auth is intact: `/app/.openviking/codex_auth.json` exists (it lives in the volume and should survive).

## 3. Snapshot (there is no nightly export; `ov-data` is the only copy)

```bash
V=<volume name from section 2>
mkdir -p /data/backups/openviking
docker run --rm -v $V:/src:ro -v /data/backups/openviking:/dst alpine \
  tar czf /dst/ov-data-pre-v0.5.0-$(date +%Y%m%d-%H%M).tgz -C /src .
ls -lh /data/backups/openviking/
```

Also keep a file-level copy of the config and policy:

```bash
docker exec $C cp /app/.openviking/ov.conf /app/.openviking/ov.conf.bak-20261011-pre-0.5.0
```

For a consistent tar, stop the container briefly (`docker stop $C`) before the tar and start it again. At idle, a live tar is probably fine for the file store, but the vector index may be mid-write if anything touches it.

## 4. Upgrade

1. Coolify → project Main → service `openviking` → edit the compose, change the image to `ghcr.io/volcengine/openviking:v0.5.0`. Update the service description (it still says v0.4.23).
2. Redeploy that one service. Do not use the "restart all" helpers; the leftover helper container `kwfbeu8a3g67cifdj9bh641d` still breaks Coolify-driven restarts.
3. `ov.conf` is written once from `OPENVIKING_CONF_CONTENT` and later edits live in the volume, so the upgrade will not overwrite our VLM, rerank or extraction settings. Confirm after boot.
4. Watch the first minutes:

```bash
docker logs -f --tail 100 $C
```

Expect no `Ignoring unknown config field` for keys we actually use. A warning for the removed `hotness_alpha` or old `session_auto_commit` keys is already past (0.4.23).

## 5. Post-checks

1. **Health and version:** `curl -s https://ov.aakashe.org/health`, then `docker exec $C ...` version, or Studio footer.
2. **Policy intact:** read `viking://user/owner/settings/user_config.json` and compare with the pre-upgrade value:
   `{"add_targets": {}, "memory_policy": {"memory_types": ["entities","events","preferences","profile"], "peer": {"enabled": true}, "self": {"enabled": true}}}`
3. **Working Memory:** decide. If you want archive overviews and checkpoints, PATCH `memory_policy.working_memory.enabled: true` explicitly. Otherwise leave the new default (off) and accept raw `messages` in archives. I recommend leaving it off first: extraction of long-term memories is unaffected, and one less LLM call per commit.
4. **Hermes prefetch recall:** start a Hermes session, ask for something only OpenViking holds, and confirm injected owner memories are quoted (same test as 2026-10-09).
5. **Claude Desktop:** one `remember` and one `find`. Note the new return text.
6. **Resources unchanged:** list `viking://resources/pilots/career-pilot-sectioned-2026-10-08/` and confirm 240 dirs / 547 files.
7. **Benchmark re-run:** the 14 occurrences in `docs/openviking-sectioned-retest-2026-10-08.md`. Baseline: `find` 10/14, `search` (min_score -1, limit ~20) 10/14 top-5, 11/14 top-10. Record the new numbers; a drop is expected to come from the quota or preset-dir changes, not data loss.
8. **Studio:** Playground is now "Filesystem" (old link redirects).
9. **Queue clean, no errors** after the above, and Hermes `agent.log` shows no new OpenViking errors.

## 6. After the server is stable (separate steps)

- **Hermes plugin:** the server upgrade does not update `/opt/data/plugins/openviking` (catalog v2.1.1). Check whether a newer catalog version exists before `hermes plugins install openviking --yes-deps`. That command is what built the PM generation that dropped the baked extras on 10-09; afterwards run `hermes pm status` and re-run the `hermes pm install --extra all --extra messaging --extra otlp --extra anthropic --extra bedrock` fix if the feature list shrank. Treat the plugin bump as its own change with its own pre-flight.
- **Do not use Hermes Quick Local;** it is pinned `<0.5` and we use the remote instance anyway.
- Update `docs/openviking-analysis.md` and the Notion migration status page (Hermes maintains it) with the new version.

## 7. Rollback

1. Change the image back to `ghcr.io/volcengine/openviking:v0.4.23` and redeploy.
2. If data looks wrong (not just config), stop the container and restore the snapshot into the volume:

```bash
docker stop $C
docker run --rm -v $V:/dst -v /data/backups/openviking:/src alpine \
  sh -c 'rm -rf /dst/* /dst/.[!.]* 2>/dev/null; tar xzf /src/<snapshot>.tgz -C /dst'
```

3. Restoring over a volume deletes everything written since the snapshot, so only do this if the upgrade corrupted state.

## 8. Open items this does not touch

Peer routing (plugin patch or separate `hermes` user), the `## Deployment` test line in `hermes_agent.md`, Desktop's stale cognee-era instructions, SOUL.md and cognee skills/crons, key rotation (owner, Jev, root, scratch MetaMCP, cognee OpenRouter), no nightly export, who made the 14:33 UTC policy change.

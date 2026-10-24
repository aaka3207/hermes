# OpenViking: one shared user, no platform peers (handover, 2026-10-24)

**Goal (user, 2026-10-24):** one OpenViking user, `owner`, shared by every agent (Hermes, Claude Desktop, Claude Code) and every
platform (Discord, photon/iMessage, CLI, dashboard). No per-platform or per-agent memory split. No drift between what Hermes's
OpenViking plugin is configured to do and what OpenViking is configured to do.

**Status:** steps 1-3 are production changes, not run. Hermes or the user runs them.

Versions this was written against: OpenViking server `v0.5.2` (restarted 2026-10-24 15:49 UTC), Hermes `v0.22.5` (base bumped in
`fa1260e8f`), OpenViking Hermes plugin at `9f150ece` (2026-10-08, stock, `/opt/data/plugins/openviking`).

## 1. Where memory behaviour is set

| Layer | Where | What it sets today | Source |
|---|---|---|---|
| Session policy | `memory_policy` on session create | **Not sent.** The plugin never passes one (no `memory_policy` in `__init__.py` at `9f150ece` or `main`). | plugin source |
| User policy | `viking://user/owner/settings/user_config.json` | `memory_types: [entities, events, preferences, profile]`, `peer.enabled: true`, `self.enabled: true`, `add_targets: {}` | read live 2026-10-24 |
| Server default | `ov.conf` `server.user_config_defaults.memory_policy` | not set | read live 2026-10-24 |
| Server switch | `ov.conf` `memory.extraction_enabled` | `true` | read live 2026-10-24 |
| Plugin peer | Hermes `memory.openviking.agent` / `OPENVIKING_AGENT` / `actor_peer_id` in `ovcli.conf` | unset (keep it unset) | 2026-10-12 note |
| Plugin recall | Hermes `memory.openviking.recall_scope` / `OPENVIKING_RECALL_SCOPE` | unset, i.e. shared recall (keep it unset) | 2026-10-12 note |

OpenViking's precedence is session > user > server default > kernel default (`docs/en/guides/01-configuration.md`). With no
session policy from the plugin, `user_config.json` is the only policy that applies to every agent. That is the place to set
things, and the only one.

Admin API facts that matter here (`docs/en/api/08-admin.md`):

- `PATCH /api/v1/admin/accounts/main/users/owner/settings` **replaces** the stored policy. Always send every field.
- Each PATCH copies the previous policy to `user_config.backup.json`. The backup is "the version before the last PATCH", not a
  known-good copy. Today it holds peers on with no type list. Do not restore from it.
- `{"memory_policy": null}` clears the override (falls back to the server default, then all types).
  `{"memory_policy": {}}` is an explicit policy that allows every type.
- A session with no policy reads the latest user policy at commit time. No restart is needed.

## 2. Why the store is split today

The plugin attaches the gateway sender to every captured user message as a peer, `<platform>.<sender id>`
(`CONFIGURATION.md`, "Gateway senders and automatic recall"; `_sender_peer`, `__init__.py:1882`). There is no setting to turn
this off and no owner exemption, in `9f150ece` or on `main`. Extraction routes user memories by the message's `peer_id`, so with
`peer.enabled: true` the owner's own platform messages are stored under peers:

- `viking://user/owner/peers/discord.459904818186551296/memories/{entities,events,preferences}`
- `viking://user/owner/peers/photon.-18478672535-4b7a8b733a13/memories/{entities,events,preferences}`

CLI, dashboard, `viking_remember` and the native memory mirror carry no peer and land at the root. Default search covers the peer
folders, but the session-start profile load reads only the root.

The only OpenViking-side control is `peer.enabled` in the user policy.

## 3. Steps

### Step 1: turn peers off in the user policy

```bash
curl -sS -X PATCH "https://ov.aakashe.org/api/v1/admin/accounts/main/users/owner/settings" \
  -H "X-API-Key: $OV_ROOT_OR_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"memory_policy":{"memory_types":["entities","events","preferences","profile"],"self":{"enabled":true},"peer":{"enabled":false}}}'
```

Then read `viking://user/owner/settings/user_config.json` and confirm it shows `peer.enabled: false` with the four types still
listed.

### Step 2: test before relying on it

The docs do not say whether, with peers off, a memory from a peer-tagged message is written to the root or skipped. Test both
gateways:

1. On Discord, tell Hermes one throwaway fact, for example "test fact 2026-10-24: my favourite test colour is teal".
2. Run `/new` so the session commits. Wait a couple of minutes for extraction.
3. Repeat over photon with a different fact.
4. Check where each fact went:
   - root: list `viking://user/owner/memories/preferences` (or `profile.md`), sorted by mtime
   - peers: list `viking://user/owner/peers/discord.459904818186551296/memories/preferences` and the photon equivalent
   - the session archive's `memory_diff.json` shows what extraction did, if the fact landed nowhere obvious

Clean up afterwards with `viking_forget` on the exact URIs.

### Step 3: decide

| Result | Do this |
|---|---|
| Both facts land at the root | Keep `peer.enabled: false`. Go to step 4. |
| Facts are dropped (no root write, no peer write) | Put `peer.enabled: true` back with the same PATCH. Peers-off would silently lose every Discord and iMessage fact, which is worse than the split. Fall back to the options below. |
| Facts still land under peers | Policy is not the lever on this version. Revert, and fall back to the options below. |

Fallback options if step 2 fails:

- Plugin patch: return an empty peer from `_sender_peer` when the sender is the owner (the gateway user IDs are already in
  `self._gateway_user_id` / `_gateway_user_id_alt`). This is a local change to a catalog plugin, and the fork was abandoned on
  2026-10-18, so any patch has to survive `hermes plugins update`.
- Promotion job: a periodic job that moves new files from `peers/*/memories/` into the root.

### Step 4: fold existing peer memories into the root

Only after step 3 keeps peers off. Read each file under the two peer folders. Re-submit anything still true with
`viking_remember`, so extraction merges it into the root instead of creating duplicates. Then `viking_forget` the peer copies.
List first and keep a copy, since nothing else backs these up.

## 4. What stays unchanged (to prevent drift)

- Do not set `memory.openviking.agent`, `OPENVIKING_AGENT`, `actor_peer_id` or `agent_id`. Any of them brings back an agent peer
  on assistant messages and mirrored writes.
- Do not set `recall_scope: peer`. It narrows recall to the current sender.
- Do not add a second policy anywhere else (`ov.conf` defaults, session policies). If a server default is ever added as a
  fallback, it must be identical to the user policy.
- Hermes's SOUL.md memory section already describes a single shared store. No change is needed there.

## 5. Checked and not a problem

- **Native memory mirror on Hermes v0.22+.** Hermes exposes `patch` instead of `replace` and sends the provider a full `replace`
  event after a patch. `native_memory_mirror.py` and `on_memory_write` are byte-identical between `9f150ece` and `main`, so
  `USER.md` / `MEMORY.md` patches keep reaching OpenViking. Upstream #6113 (2026-10-16) only added a test for this.
- **Cron, subagent and flush contexts** do not upload turns or mirror memory automatically. Explicit `viking_*` calls still write.

## 6. Rollback

Step 1: PATCH the same body with `"peer":{"enabled":true}`. That is the exact policy that was live before this handover.

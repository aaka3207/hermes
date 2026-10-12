# OpenViking: Hermes and Claude Desktop sharing one user, findings of 2026-10-10

Question: is the cross-contamination between Claude Desktop and Hermes in OpenViking solved by using peers?
Short answer: **not shown yet, and probably not on its own.** The one decisive test was confounded by a user policy
that forbade peer writes (section 6). Re-run it before drawing a conclusion.

Related notes (read these for the earlier analysis this builds on):
- `claudedocs/research_openviking_peers_hermes_plugin_20261008.md`: the 2026-10-08 peer analysis and options table. This doc tests its predictions.
- `docs/openviking-analysis.md`: the OpenViking architecture review (§8 on templates understates the account-template API).
- `docs/openviking-hub-trial.md`: the Career hub trial and why `memory.extraction_enabled` was off.
- `claudedocs/research_openviking_20260926.md`: the first OpenViking research (extraction can merge or delete hand-written records).

Labels: VERIFIED = read directly this session (server file, log or API). INFERRED = reasoned from code or docs, not observed.

## 1. What the sources say

- **Blog post** ("OpenViking User / Peer", blog.openviking.ai): a peer is an interaction object under a user. The example is a support bot where each
  customer is a peer at `viking://user/{uid}/peers/{peer}/memories`. Write tagging is `peer_id` on session messages; read filtering is `actor_peer_id`
  (the `X-OpenViking-Actor-Peer` header). It addresses one agent serving many people, not two agents on one user.
- **Capability reference** (`docs/en/agent-integrations/16-capability-reference.md`): Hermes sends no peer unless `OPENVIKING_AGENT`, a linked
  OpenViking peer, or the YAML `agent` key sets one. No integration enforces read restrictions at the tool level; server-side ACLs
  (`get_acl`, `set_acl`) are the authoritative enforcement. MCP `forget` and deletes refuse URIs hidden by the caller's actor-peer view.
- **Hermes plugin doc**: default is no peer, with explicit memories written to `viking://user/<user>/memories/`. `memory.openviking.agent: <name>`
  gives "separate assistant context". With no peer, default search covers user memory and every peer under the same user.
- **Maintainers** (discussion #4602): a user is a credential and data-isolation boundary, not an entity type; agents that need their own isolation
  are their own users; the peer is "a convenience feature, not an ontological category". Write routing comes from `peer_id` on user-role messages;
  the actor-peer header is a read view only. One operator running Hermes reported our symptom (100% of extraction at user level because stored
  messages carried no `peer_id`) and is rebuilding as one user per agent.
- **Open issues:** #5613 (extraction omitting `peer_id` widens to the user root), #5433 (`peer_scope: actor` still returns other peers' memories),
  #5241 (session-start profile ignores the peer), #3326 (closed; MCP `remember` does not route by actor peer). RFC #4565 and PR #3540 derive
  peers from the git origin, which suits coding agents, not our case.
- No one has posted a Hermes plus Claude Desktop on one user write-up.

## 2. Plugin behaviour (VERIFIED on the server)

Installed plugin: catalog v2.1.1 at `/opt/data/plugins/openviking` (the bundled v2.0.0 read on 2026-10-07 is gone).
- **Peer resolution** (`__init__.py:891`): env var, then the linked ovcli profile, then `config.yaml`, then the built-in default. `ovcli.conf.owner`
  holds only `url` and `api_key`, so `memory.openviking.agent` in `config.yaml` takes effect unless `OPENVIKING_AGENT` is set (it is not).
- **Where the peer goes:** `_messages_to_openviking_batch` tags assistant messages with the configured `agent`. The user message gets a peer only
  for gateway senders (the existing `photon.-…` peer). Owner turns from Desktop or the CLI carry none.
- **`viking_remember`** (`_tool_remember`): opens a throwaway session `hermes-remember-<id>`, posts one bare user message, commits at once.
  The peer is never attached, so the setting cannot affect where it lands.
- **Commit triggers:** session end, session switch (`/new`, `/reset`, `/resume`, `/branch`, compression), a live token threshold, and shutdown.
  Log example: `committed after live token threshold (7 turns)` at 09:22, then `committed on session end (3 turns)` at 09:25 CDT.
  An earlier claim of "no mid-session commit" was wrong.

## 3. Config change made

`memory.openviking.agent: hermes` was added to `/opt/data/config.yaml` at 09:01 CDT on 2026-10-10 (production, applied by the user).
Agent starts at 09:02 loaded it: assistant messages in session `20261010_090231_030849` carry `"peer_id": "hermes"`.
Rollback: delete the line.

## 4. Test A: `viking_remember` (session `hermes-remember-5544b32eccf5`, task `64edfb86-3043-4b8b-a0a2-9927d23a2026`)

The message carried no `peer_id`. Extraction (14:07 UTC) wrote **two updates at the user root**, nothing under `peers/hermes/`:
- `memories/entities/project/hermes_agent.md` gained a `## Deployment` line ("Runs on the user's home server box managed by Coolify"). This is the
  test marker. **Cleanup pending:** remove that line. As of the last check it was still there.
- `memories/entities/project/openviking_migration.md` was reformatted and condensed without being asked (for example `does not exist` became
  `missing`). The facts are intact but the text drifted. Not worth reverting.

Another `hermes-remember-5c97e3e4766b` session exists that was not examined.

## 5. Test B: a normal Desktop session (`20261010_090231_030849`)

Commits at 14:24:51 UTC (archive_001) and 14:26:43 UTC (archive_002). **Everything went to the root; nothing went to `peers/hermes/`.**
- Adds: `events/2026/10/10/voter_registration_need.md`, `voter_registration_confirmed.md`, `ent_procedure_scheduled.md`.
- Updates: `profile.md` (twice), `entities/person/dixon.md`, `entities/project/openviking_migration.md` (rewritten again),
  `preferences/ameer/memory_isolation.md` (three bullets cut to one, including the "avoid `remember` for Hermes content" line extraction had inferred).
- The voter and ENT items are genuine owner facts, so the root is correct for them. The operational drift is limited to the two project and
  preference files above.

## 6. The confound: the user policy

`viking://user/owner/settings/user_config.json` was overwritten at 14:33:51 and 14:33:57 UTC on 2026-10-10 by two
`PATCH /api/v1/admin/accounts/main/users/owner/settings` calls from the internal address `10.0.21.3` (not Hermes: no trace in `agent.log`; the
caller is unidentified, plausibly the playground UI).
- **Before** (`user_config.backup.json`): `memory_types: [entities, events, preferences, profile]`, `peer.enabled: false`, `self.enabled: true`.
- **After:** `peer.enabled: true`, `self.enabled: true`, no `memory_types` list (all types allowed, including `cases` and `patterns`).
- The old policy was in force for every extraction in sections 4 and 5. With `peer.enabled: false`, extraction could not write to a peer whatever the
  plugin sent, so "the peer didn't redirect" is **inconclusive**. The `claude` user has its own config from 2026-10-08 03:47 UTC (nine types, peers on).
- Our earlier docs never record who set the original policy.

## 7. Options

| Option | Effect | Cost |
|---|---|---|
| Keep one user, add instructions to Claude Desktop (draft below) | Cheap, covers reads | Does not stop entity-file drift |
| `agent: hermes` peer (done) | Labels assistant turns; helps only if extraction routes by it (untested under the new policy) | `viking_remember` is unaffected; Desktop's no-peer search still sees all peers |
| Restore `memory_types` to the four-type list | Keeps `cases` and `patterns` out | Applies to Desktop `remember` too |
| Plugin patch: set the user `peer_id` on owner turns | All Hermes extraction under `peers/hermes/` | Owner facts from Hermes stop updating the root `profile.md`; lives in a catalog plugin that updates can overwrite |
| Separate `hermes` user | Real boundary; upstream's answer | Needs a bridge (`resources/` or ACLs, ACLs untested) for shared facts |

Current direction (user's call, 2026-10-10): tell Claude the store is shared and to ignore Hermes junk. Draft for Desktop's custom instructions:

> OpenViking memory is shared with Hermes, an autonomous agent that writes to the same user space. Treat memory as a mixed source. Hermes's session
> activity, tool output, config chatter and project-status notes may appear in profile, preferences, entities and events. If an item looks operational
> (server paths, plugin or config details, test markers, trial logs, "as of" status lines), ignore it unless I ask about that topic. Facts about me
> (health, address, appointments, preferences) are reliable even when they came from a Hermes session. If a memory conflicts with this chat, trust the
> chat and tell me which memory looks wrong. Never write Hermes-operational details with `remember`.

## 8. Open items

1. Re-run Test B under the new policy: short Hermes session with owner facts plus operational chatter, `/new`, then read `memory_diff.json`.
2. Decide `memory_types` (restore the four-type list or leave open) after step 1.
3. Remove the `## Deployment` test line from `hermes_agent.md` (needs the user's go-ahead; it is the owner's store).
4. Apply the Desktop instructions block; Desktop's current instructions date from 2026-09-29 and still reference cognee.
5. Hand over a matching SOUL.md note ("Desktop reads this store; avoid `viking_remember` for operational details"). SOUL.md is Hermes-maintained, so text only.
6. Find out who made the 14:33 UTC policy change, and whether anyone had set the original policy deliberately.
7. Read-only check of `get_acl` on `viking://user/owner/` to see whether ACLs could bridge two users.

## 9. Later the same day: Codex VLM, re-test, peer vs actor peer

**VLM switch.** OpenViking's VLM moved to Codex (ChatGPT sign-in): `provider: openai-codex`, `model: gpt-5.6-terra`, `api_base: https://chatgpt.com/backend-api/codex`,
`temperature: 0.0`, `max_retries: 2` in `/app/.openviking/ov.conf` (backup `ov.conf.bak-20261010-codex`; OAuth creds in `/app/.openviking/codex_auth.json`). `doctor` passed.
`gpt-5.6-terra` is offered by the wizard but is not on the Codex models page. Quota is shared with Hermes. Embedding and rerank are unchanged. `reasoning_effort` is unset.

**Re-test under the new policy (peers on, no type list, Terra), sessions at 15:25-15:28 UTC.** A Desktop session (Hermes building the Project Cabinet plugin) wrote
8 adds and 1 update, **all at the user root, nothing under `peers/hermes/`**. `cases`, `trajectories` and `experiences` now appear because the `memory_types` restriction is gone.
`openviking_migration.md` was condensed again (the original is recoverable from `archive_001/memory_diff.json` `before`). So the peer test is no longer confounded, and it is negative.

**Why (VERIFIED in plugin v2.1.1, `_messages_to_openviking_batch`).** `emit()` gives `peer_id` to assistant messages only (`assistant_peer_id`), and to user messages only via `user_peer_id`
(gateway senders). Tool calls and tool results are folded into **assistant** messages tagged `hermes`; recall-tool calls are dropped. Extraction routes by user-role messages, so
Hermes tool activity is tagged but not routed. Where the LLM omits `peer_id`, output goes to the root (#5613).

**Peer vs actor peer (docs: 11-multi-tenant, 05-sessions, 02-client).**
- `peer_id` (message field) is the write-side hint. `actor_peer_id` (`X-OpenViking-Actor-Peer`, or `actor_peer_id` in `ovcli.conf`) is a read-side view that also blocks fs operations on other peers.
- The plugin maps `actor_peer_id` and `agent` to the same value (`__init__.py:652, 987`), so adding it to `ovcli.conf.owner` duplicates `agent: hermes` and only narrows reads. Do not set `actor_peer_id` and `agent_id` together.
- `peer_enabled` on memory schemas is locked to deployment defaults (08-admin), so there is no account-level lever.

**Decision (user, 2026-10-10):** restore the `memory_types` list. Body to PATCH (production, handed over, not run):

```bash
curl -X PATCH "https://ov.aakashe.org/api/v1/admin/accounts/main/users/owner/settings" \
  -H "X-API-Key: $OV_ROOT_OR_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"memory_policy":{"memory_types":["entities","events","preferences","profile"],"self":{"enabled":true},"peer":{"enabled":true}}}'
```

It keeps peers enabled (the pre-14:33 policy had `peer.enabled: false`). It also applies to Desktop `remember`. Read the file back afterwards to confirm the PATCH merged rather than replaced
(`viking://user/owner/settings/user_config.json`); `add_targets: {}` should survive. Rollback: PATCH `memory_types` back to the full list, or restore from `user_config.backup.json`.

## 10. Remaining open items

Real routing needs a plugin patch (`peer_id` on user messages) or a separate `hermes` user; both deferred. Still pending: the `## Deployment` line, the Desktop instructions block, the SOUL.md note,
who made the 14:33 UTC change, the ACL read check, stale Hermes model names (`gpt-5.4-mini` retired, `gpt-5.5` retires 2026-10-14), owner/Jev key rotation.

Nothing was written to OpenViking this session by Claude. The `viking_remember` call and its session came from Hermes at the user's request.

## Update 2026-10-12

- **Gateway senders route.** Confirmed by the user and in the store: `viking://user/owner/peers/discord.<id>` (events) and `peers/photon.<id>` (events plus `preferences/ameer akashe/bookmark_workflow.md`).
  Plugin v2.1.1 attaches the gateway sender as the peer on user messages (`CONFIGURATION.md`, "Gateway senders and automatic recall"). So the leak described above is limited to turns **without a sender**
  (dashboard/desktop chats, CLI) and `viking_remember`, which still land at the user root. Peer content is searchable (default search covers all peers under the user) but is not in the session-start
  listing, which reads only the root `profile.md`, `preferences/`, `entities/`.
- **`agent: hermes` is no longer in `config.yaml`.** The live file, and the 2026-10-12 pre-change backup, have `memory.openviking` with only `use_ovcli_config` and `ovcli_config_path`; `ovcli.conf.owner` has
  `url` and `api_key` only; there is no `peers/hermes` directory. When or why it was removed is not established. Hermes is sending no assistant peer today.
- **`recall_scope`** (`shared` / `peer`) is unset. `peer` would narrow automatic recall to common memory plus the current gateway sender; it does not change access or writes.
- **Peers are not isolation, per the multi-tenant doc:** "`actor_peer_id` ... does not make that user's own memories or shared resources private". ACL covers only `viking://resources/...`, not memories.
- **A two-agent design was drafted and shelved:** `docs/openviking-two-agent-memory-architecture-2026-10-12.md`.
- **Unchanged decision:** accept the leak (user, 2026-10-10).

## Sources

- blog.openviking.ai/post/openviking-user-peer-model/
- github.com/volcengine/OpenViking: `docs/en/agent-integrations/16-capability-reference.md`; discussions #4602, #4565, #2253; issues #5613, #5433, #5241, #3326
- hermes-agent.nousresearch.com/docs/plugins/openviking
- Server reads on 2026-10-10: plugin `/opt/data/plugins/openviking/__init__.py` (v2.1.1), `/opt/data/config.yaml`, OpenViking container
  `/app/.openviking/data/viking/main/user/owner/settings/`, access logs, session `memory_diff.json` files

# OpenViking peers, memories and the Hermes plugin: would one user plus a Hermes peer work?

Date: 2026-10-08. OpenViking v0.4.23 (sha df32bf6e). Hermes plugin v0.4.23 bundle, `/opt/hermes/plugins/memory/openviking` v2.0.0.
Method: two read-only subagents (upstream source/docs/issues; plugin source on the server), synthesized here.
Labels: VERIFIED = read in code or docs by a subagent (not re-read by me). INFERRED = reasoned, not confirmed.

## Question

Can Hermes use the OpenViking memory plugin against the same user as Claude Desktop (`owner`), with Hermes-operational
memories kept in a Hermes peer subtree and out of the general profile (`profile.md`, entities, preferences, events)?

## Answer

**Not reliably.** Confidence that one user plus a peer keeps the profile clean: low. A peer is a view filter and a
write hint, not a wall. Hard separation needs a separate OpenViking user for Hermes.

## Findings

### Server (upstream)
1. A peer is a content scope inside a user: `viking://user/<uid>/peers/<peer>/{memories,resources}` (VERIFIED, `11-multi-tenant.md`).
   There is no per-agent memory space. `viking://agent/` holds only shared skills.
2. Most memory types (profile, preferences, entities, events, identity, soul) can be written at the user root or under a peer.
   Only `cases` is explicitly self-only (VERIFIED for cases; defaults for the rest not fully checked).
3. **The actor-peer header does not route extraction.** Write scope comes from the session `memory_policy` plus each
   message's `peer_id` (VERIFIED, `memory_isolation_handler.py`; maintainer statement in #3326).
   - A user-role message with `peer_id` P writes to `peers/P`. A user message with no `peer_id` writes to the user root.
   - Assistant messages never own memory. They only widen the allowed peer set.
   - If the extraction LLM omits `peer_id` on an operation and `allow_self=True` (default), it goes to the user root.
     Open issue #5613 documents this leak.
4. `memory_policy` (`self.enabled`, `peer.enabled`, `memory_types`) can be set per session, per user (admin endpoint)
   or as a deployment default; precedence session, user, deployment (VERIFIED, docs). With `self.enabled=false`, writes can
   only go to peers.
5. **Read visibility.** With an actor peer set, memory search covers the user root plus that peer, not the peer alone.
   Without a peer (Claude Desktop), the default search covers the user root, which includes ALL peers' memories.
   So Hermes-operational memories would appear in Desktop searches unless Desktop targets `viking://user/<u>/memories/` explicitly.
   Open bug #5433: `peer_scope="actor"` does not isolate recall.
6. Claude Desktop MCP `remember` uses a temporary session with no `peer_id`, so it writes to the user root. Maintainers say
   MCP will not support peers (#3326). Good for owner facts.
7. Docs say peers are not an access boundary: for true isolation use distinct user connections (`11-multi-tenant.md`,
   Hermes plugin README).

### Plugin (on the server)
1. Inactive today: `memory.provider: cognee`, no `memory.openviking` section, no `OPENVIKING_*` variables.
2. "agent" and "peer" are the same setting. It sends `X-OpenViking-Actor-Peer` and puts `peer_id` on **assistant** messages only.
   User messages never carry it, except gateway senders as the README describes (per upstream agent; plugin agent saw no such code in what it read, so UNRESOLVED).
3. **Owner turns land at the user root.** CLI/owner user messages have no `peer_id`, so extraction treats them as user facts
   whatever `agent` is set to (INFERRED from 2 plus the server rules). `viking_remember` also sends a bare user message with no peer.
4. The session-start block always reads `viking://user/<uid>/memories/{profile.md,preferences,entities}` and ignores the peer
   (VERIFIED). Hermes's own mirrored writes go under `peers/<agent>/`, so they never show in that block (open #5241, fix PR #5246).
5. The plugin has no setting to limit extraction types or scope, and no way to mark a turn as operational.
6. One plugin instance equals one OpenViking user.

## Options

| Option | Keeps profile clean | Hermes sees owner facts | Cost |
|---|---|---|---|
| Same user, default policy, `agent: hermes` | No (#5613, owner turns at root) | Yes | Leaky |
| Same user, `self.enabled=false` policy | Yes for Hermes | Yes | The user-level policy also hits Desktop `remember`, which has no peer, so Desktop saves would be skipped. Defeats the goal. A session policy would avoid that, but the plugin does not set one (INFERRED). |
| **Separate `hermes` user** | **Yes (physical)** | **No, unless bridged** | Needs a bridge for general facts |

## Recommendation

Use a separate `hermes` user for the plugin (already exists for MCP). Hermes gets its own `profile.md`, preferences and
operational memories, and your `owner` profile stays untouched. For the shared facts, bridge rather than share the user:
- Publish a curated owner profile into `viking://resources/` (account-shared) and enable `recall_resources: true`, or
- let Hermes keep its own copy of the few facts it needs.

Not verified, test before adopting: ACLs on user memory (could give `hermes` read access to selected `owner` paths),
the admin endpoint for per-user `memory_policy`, plugin behavior with `agent` set and no gateway sender,
and the default `peer_enabled` for each schema.

## Suggested test (read-only analysis first, then one throwaway commit)

1. Create a scratch session as the `hermes` user with a few operational messages and a few personal ones; commit.
2. Read `memory_diff.json`; confirm nothing lands under `owner`.
3. From Desktop (`owner`), search for the Hermes content; confirm it is not returned.
4. Check what the session-start block shows for `hermes`.

Gate: cognee verdict due about 2026-10-14. Production config and key steps are handed over, not run.

## Sources

Upstream: `docs/en/concepts/11-multi-tenant.md`, `02-context-types.md`, `api/05-sessions.md`, `api/08-admin.md`,
`openviking/session/memory/memory_isolation_handler.py`, `memory_policy.py`, `core/retrieval_targets.py`,
issues #5613, #5241 (PR #5246), #5433, #3326, #3075, #5382, #4043, #5104, #5411.
Plugin: `__init__.py` lines 83-100, 249-261, 1828-1865, 1960-1990, 2342, 2417-2449, 2595-2635; README.

## Addendum: can prompt customization fix it? (same day)

Sources: docs.openviking.ai (`guides/10-prompt-guide`, `api/08-admin`, `concepts/08-session`, `concepts/11-multi-tenant`) plus a
source read of v0.4.23 by a subagent. Our own `docs/openviking-analysis.md` §8 understated the account-template API; see below.

### What can be customised
- **The global extraction instruction is hard-coded** in `session_extract_context_provider.py` `instruction()`. Not file-overridable.
  `prompts.templates_dir` swaps other prompts (compression, retrieval, semantic, vision), not this one. No `custom_prompt` key exists.
- **Per-type schema descriptions are the extraction rules the model sees.** Free text up to 50,000 characters, injected into the
  output-schema field description. Compliance is up to the model.
- **`memory.custom_templates_dir`** (ov.conf, restart needed): a schema with the same `memory_type` replaces the built-in; a new
  name adds a type. Only `.yaml`/`.yml` directly in the directory.
- **Account template API exists in 0.4.23:** `GET|PUT|DELETE /api/v1/admin/accounts/{id}/memory-templates/{type}`, root/admin only,
  account-wide, applied at extraction start, no restart. Editable: type and field descriptions (and `content_template` for events,
  soul, identity) for profile, events, preferences, entities, soul, identity. Cannot add types or fields.
- **`memory_policy.memory_types`** can be set per session (`create_session` options), per user (`user_config`), or as a server default.
  Types outside the list are not shown to the extraction model and the handler rejects writes to them.

### What the source says about routing
- Only user-role messages own memory. Assistant messages tagged `peer_id=hermes` just add `hermes` to the allowed peers.
- Owner messages have no `peer_id`, so they resolve to the user root.
- For profile, preferences and entities, an omitted LLM `peer_id` goes to the user root (#5613).
- The built-in `profile` description already says to extract only facts the participant states and never infer from assistant text.
  `preferences` and `entities` have no such guard, and "preferred tool/workflow" under preferences is a natural sink for Hermes notes.
- A type has no "peers only" flag. `peer_enabled: false` forces self; `self.enabled=false` is a global switch.

### Ranking for keeping operational content out of `profile.md`
1. Separate OpenViking user for Hermes: deterministic.
2. Per-session `memory_policy.memory_types` limited to a Hermes type: strong, same user. But the plugin never calls create-session with
   options (sessions are created implicitly on first message), so this needs a plugin patch; a user-level policy would also hit Desktop.
   Costs capture of user facts stated in Hermes sessions.
3. Custom `hermes_ops` type (`stage: user`, `peer_enabled: true`, own directory) plus exclusion text in profile, preferences and entities:
   gives operational content a home; model-dependent, and an omitted `peer_id` lands it at the user root (harmless to `profile.md`).
4. Description-only edits: weakest. #5613's reporter saw 0/5 leaks, 5 runs, template text not posted. #5599 saw a description
   instruction fail to change output at all with deepseek-v4.1-flash.

### Test (any prompt-based option needs it; routing is model-dependent)
Scratch session with Hermes-style messages, at least 20 commits on the production extraction model, then read
`memories/{profile.md,preferences,entities,operational}` and `peers/hermes/memories`, and inspect `memory_diff.json`.
Include a control run with a real user fact in the same session. Confirm the edited descriptions reach the model.

### Revised recommendation
Prompt customization is a defence in depth, not a boundary. A custom `hermes_ops` type plus tightened descriptions on the same
user could work if you accept probabilistic separation and verify it with the 20-run test. For a guarantee, use a separate
`hermes` user. The `memory.extraction_enabled` switch is now on, and `identity.md`/`soul.md` templates are also account-editable.

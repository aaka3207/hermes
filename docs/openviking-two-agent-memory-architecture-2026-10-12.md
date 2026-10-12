# Two-agent OpenViking memory: private per agent, merged durable facts (2026-10-12)

Status: **design, shelved by the user on 2026-10-12 ("it's fine"); nothing applied.** Kept as a record of the constraints. The setup it describes stays as is: one shared `owner` user, with gateway senders routing to their own peers. Production writes and deploys are gated. Items marked UNVERIFIED are not tested.
Sources: `docs/en/concepts/15-acl.md`, `docs/en/api/12-acl.md`, `docs/en/concepts/11-multi-tenant.md`, the capability reference (`16-capability-reference`),
the Hermes plugin `CONFIGURATION.md` at `9f150ec` (v2.1.1, the installed version), and the live store on 2026-10-12.

## 1. The goal

1. Hermes has its own memory, auto-injected, not contaminated by Claude.
2. Claude has its own memory, auto-injected, not contaminated by Hermes.
3. A scheduled job promotes durable, agent-agnostic facts from both into something both read.
4. Resources are readable by both.

## 2. Why one user with peers does not get there

| Need | What OpenViking does | Source |
|---|---|---|
| Each agent's writes stay in its own space | The Hermes plugin tags only **assistant** messages with the configured peer. Owner turns and `viking_remember` carry none and land at the user root. Claude Desktop connects over MCP and would need an `X-OpenViking-Actor-Peer` header (UNVERIFIED that the connector can set one). | plugin + peer findings 2026-10-10 |
| Each agent's injection reads its own space | Session start reads only the root `profile.md`, `preferences/`, `entities/`. Fix is upstream #5246 (open, unmerged; bug #5241). | plugin source; GitHub |
| Peers isolate | **No.** "`actor_peer_id` ... does not make that user's own memories or shared resources private." The peer filter is a read view inside one user. | multi-tenant doc |
| ACL shares memory | **No.** ACL applies only to `viking://resources/...`. `viking://user/{id}/...` does not accept ACLs. | ACL doc |
| Claude Desktop auto-injects | **No.** MCP gives tools only: "no automatic recall, capture, or commits". Claude Code has a plugin with session-start injection; Desktop does not. | capability reference |

Conclusion: the isolation unit is the **user**. Peers are a convenience. A fork of the Hermes plugin would still leave the Claude side and #5246 unsolved.

## 3. Proposed layout

```
account main
├── user hermes      memory root auto-injected by the Hermes plugin
├── user claude      memory root; auto-injected only by Claude Code's plugin (not Desktop)
├── user owner       existing; see section 7 (migration, undecided)
└── viking://resources/...        shared by default across the account
      └── shared/                 merged durable facts (section 5)
```

- **Hermes plugin** gets the `hermes` user's key via its linked `ovcli.conf` (one plugin instance is one user).
- **Claude clients** get the `claude` user's key. The existing `claude_ai_opneviking` connector would be repointed.
- **Cron** holds both user keys and acts as each in turn. A USER key reads its own data plus shared resources; ROOT cannot read tenant data in `api_key` mode, and ADMIN is bound to its own user. No role escalation is needed.

## 4. Settings that matter

| Setting | Where | Default | Role here |
|---|---|---|---|
| `memory.openviking.agent` | Hermes `config.yaml` (or `OPENVIKING_AGENT`, or linked `actor_peer_id`) | none | Tags **assistant** messages with a peer and scopes mirrored memory writes under `peers/<agent>/`. Optional once users are separate. |
| `memory.openviking.recall_scope` | Hermes `config.yaml` / `OPENVIKING_RECALL_SCOPE` | unset (legacy: shared) | `shared` = common + all peers. `peer` = common + the **current gateway sender's** memory; with no sender, common only. Narrows automatic recall only; it does not change access, explicit `viking_*` tools, mirroring or credentials. |
| `recall_resources` | Hermes plugin config | `false` | Needed for auto-recall to touch `viking://resources`, including the shared facts. Also pulls in the 240-dir Career pilot. |
| `recall_max_injected_chars` / `recall_limit` | plugin config | 4000 / 6 | Hard ceiling on retrieved context per turn. |
| `profile_token_budget` | plugin config | 6000 | Session-start memory budget. |
| `commit_token_threshold` | plugin config | 20,000 | Background commit trigger. |
| `acl.enabled` | account setting | off | Only needed to *restrict* resources. Off = account-wide visibility under namespace rules. |

Gateway senders (Discord, photon) already route per sender: the store has `peers/discord.<id>` and `peers/photon.<id>`. That works today and needs no setting.

**Finding, unexplained:** the live `config.yaml` (and the 10-12 backup) has `memory.openviking` with only `use_ovcli_config` and `ovcli_config_path`.
There is **no `agent:`** and no `recall_scope`, and `ovcli.conf.owner` has only `url` and `api_key`. The 2026-10-10 note says `agent: hermes` was set. There is no `peers/hermes` directory in the
store, which fits it not being set. I have not established when or why it went. Hermes is therefore sending no assistant peer today.

## 5. Where merged facts go

| Option | Both read it | Auto-injected at session start | Cost |
|---|---|---|---|
| A. Fan-out: cron writes the fact into **both** users' `profile.md` / `preferences/` | yes | **yes**, within the injection budget | two writes; `profile.md` must stay short (shared plugins show about the first 8 lines) |
| B. Shared docs under `viking://resources/shared/` | yes (default) | **no**, retrieval only | one write; Hermes needs `recall_resources: true` or an explicit `viking_search` |

Recommendation: **A for the handful of facts that must always be present** (who the user is, standing rules), **B for everything else.** Keep A small on purpose.

## 6. The merge cron

Inputs: both users' memories changed since the last run (by mtime or URI listing). Output: promoted facts (A and B), plus a provenance record.

Rules it needs, none of which exist yet:
- **Agnostic test:** a fact is promoted only if it is about the user, durable, and not tied to either agent's tooling. Hermes environment quirks and Claude-specific workflow stay put.
- **Dedupe:** write through the normal extraction path so OpenViking merges within each user; keep a content hash of promoted facts.
- **Provenance and loop guard:** tag every promoted fact with its source user and URI, and skip anything carrying that tag so the job never re-promotes its own copies.
- **Dry run first**, and a diff report to Notion or Discord before the first live write. Nothing is deleted by the job.
- Cron-context sessions do not write to OpenViking from the Hermes plugin (`cron`/`subagent`/`flush` are read-only), so the job must call the REST/MCP API directly with the keys.

Where it runs: a Hermes cron job is the available scheduler, but it must not run through the plugin's write path. A standalone script on the server is the cleaner option. Not decided.

## 7. Open decisions

1. **What `owner` becomes.** It holds the Career pilot resources and all current memories. Options: keep it as the canonical merged store and seed `hermes` and `claude` from it; or retire it after migration. The Hermes cutover (2026-10-08) deliberately chose the owner user for one shared memory, so this reverses it.
2. **Claude Desktop.** Without hooks it cannot auto-inject. Either keep its instruction text ("search OpenViking at the start of a conversation") or accept Claude Code as the injecting client.
3. **Key hygiene.** The owner key was pasted in chat on 2026-10-08 and is unrotated; the new users get new keys, so this is the moment to rotate.
4. **Nightly export.** Still none. Add before moving data.

## 8. Checks before building (all cheap, none run)

1. Create a throwaway user and confirm a USER key can **write** under `viking://resources/shared/` with `acl.enabled` off (UNVERIFIED; the docs say "namespace rules" only).
2. Confirm the Hermes plugin runs against a second user key without side effects on the `owner` data.
3. Find out why `agent:` is absent from `config.yaml`.
4. Confirm whether the Claude connector can send `X-OpenViking-Actor-Peer`. If it can, peers become usable as a fallback on the Claude side.
5. Measure how many facts the merge job would promote from the current `owner` store (dry run).

## 9. What this does not solve

- Claude Desktop auto-injection (needs host hooks).
- Anything the extraction model classifies badly. Separate users stop *cross-agent* contamination, not misclassification within one agent.
- Retrieval ceilings: 4,000 characters and 6 items per turn.

# OpenViking one-hub trial: replacing a Notion hub

> **Scope update — 2026-10-08:** This document is a narrower, still-unrun experiment about storing a prose hub in OpenViking; it is not the overall project decision framework. The user's main interest is OpenViking as an agent-context layer. Notion may remain for pages Hermes creates for reading scripts or making checklists. Do not expand this trial into proving parity for all Notion databases, views, or human-facing workflows. Career Strategy is the selected hub for the broader pilot; representative workflows can be tested later. The isolated copy remains disposable and Notion remains authoritative during evaluation. The user's single-user setup makes multi-user permission parity non-gating; retain basic privacy safeguards.

**Status: a plan, not run.** Nothing here has been executed. Claims carry the
labels used in `openviking-analysis.md`: **VERIFIED** (read in code or docs),
**INFERRED**, **UNKNOWN**. This plan turns the UNKNOWN and INFERRED ones into
measured ones.

Companion documents:

* `docs/openviking-analysis.md`: the v0.4.23 re-check and the §9 test list this plan draws on
* `claudedocs/research_openviking_20260926.md`: the original research

---

## 1. The question

Can one Notion hub (a prose context document that Claude Desktop and Hermes
load as context) live in OpenViking as a plain file, with both agents reading
and editing it, and with **no pointer records, no session distillation and no
consolidation jobs**?

**In scope:** prose hubs only. Notion databases (tasks, relations, views) stay
in Notion.

**Not in scope:** replacing cognee in production. Cognee keeps running through
the whole trial. The trial runs beside it and is read-only with respect to it.

## 2. How it is meant to work

```
Claude Desktop ──OAuth 2.1──►  /mcp  ◄──MCP entry, no memory.provider──  Hermes
                                │
                  write · edit · read · grep · find · forget
                                │
              viking://resources/hubs/<hub>/        (curated, verbatim)
                                │
          RAGFS files (source of truth)  +  vector index (rebuildable)
                 .abstract.md / .overview.md sidecars (LLM-made, advisory)
```

Design rules:

1. **Both agents write only through MCP `write` and `edit`.** No session
   capture, no `remember`.
2. **Hermes connects through MCP only**, with no `memory.provider`. Capture is
   then structurally impossible, because the bundled plugin's `sync_turn`,
   commit, mirror and prefetch all disappear. Hide `remember` with
   `tools.include`.
3. **`memory.extraction_enabled: false` in `ov.conf`.** This is the one reliable
   global off switch (per-user `memory_policy` is not reliable).
4. **Hubs live under `viking://resources/hubs/`, not `memories/`.** A write
   under `memories/` gets memory handling (`MemoryFileUtils.write`,
   `sync_memory_resource_refs`) whose effect on a verbatim file is UNKNOWN.
   Whether `resources/` is the safer home is test 3 below.
5. **One user for both agents.** Authorize Desktop's OAuth with the Hermes
   user's key. Do not set an actor peer on either side; a peer restricts
   search to its own subtree.
6. **A hit is a pointer.** Always `read` the URI after a search. Default
   retrieval can return the L0 abstract, which may omit an id or date that only
   lives in the body.

Known gaps this design cannot close:

* **`remember` cannot be hidden from Claude Desktop.** The server has no tool
  allowlist and `tools/list` is not filtered per identity (VERIFIED). Only an
  instruction keeps Desktop away from it. A per-tool toggle on Desktop's side
  is plausible but not verified.
* **MCP `write` takes no `tags`.** Tags are REST only (VERIFIED).
* **Dates are not free.** No server-set `created_at`, `updated_at` or
  `created_by` was found in the write path. Put dates in the file body.

## 3. Phase 0: prerequisites

Do these before anything else. Both are cheap.

| Step | Action | Why |
|---|---|---|
| 0.1 | Find which hermes-agent commit `:latest` resolved to (`hermes --version`, `install-stamp.json` in the container) | The deployed plugin version is unknown, and the bundled plugin is expected to be removed upstream |
| 0.2 | Check whether Hermes cron sessions receive MCP tools under the current toolset config | Decides whether cron jobs can maintain hubs at all |
| 0.3 | Pick **one low-stakes hub** to migrate | A wrong pick loses something that matters. Choose something you can recreate from Notion in minutes |
| 0.4 | Export that hub from Notion as markdown and keep the export | This is the rollback copy |

## 4. Phase 1: throwaway server, write and read semantics

Run a local OpenViking v0.4.23 in `api_key` mode with `oauth.enabled` and two
test users. **Never against production data.** Set
`memory.extraction_enabled: false` unless the test says otherwise.

Pin the version in the compose file. Releases ship roughly every 5 days and most
carry migration notes. A vector-format change is one-way.

Tests (numbers refer to `openviking-analysis.md` §9):

| # | Test | Pass condition |
|---|---|---|
| 1 | Authorize OAuth with user A's key, `write viking://resources/hubs/probe.md`, `read` it with A's plain key. Repeat with user B | A sees both reads, B sees a different space |
| 2 | Write markdown with a date line and fenced code via MCP and via REST. Compare `read?raw=true` | Bytes identical. Record whether `.abstract.md` and `.overview.md` appear, and how long they take |
| 2b | Repeat under `memories/` | Record any difference from `resources/`, including `resource_refs` metadata |
| 3 | Check what `created_at` and `updated_at` a write produces. Check whether a trailer-supplied date round-trips | Know whether dates are usable, or whether they must live in the body |
| 13 | BM25 `search_type="keywords"` vs `grep` on a UUID, a Notion page id and a short title | Both return the file for the exact id. Note which is faster and which misses |
| 12 | Rotate the user key | `/mcp` returns 401 and Desktop re-prompts |
| new | Two clients `edit` the same file at nearly the same time | Learn whether the second write loses the first. Locking behaviour is UNKNOWN |
| new | `write` with `wait=true` then `search` | The new content is searchable on return |

**Gate:** stop and re-plan if bytes are not preserved, or if the two-client
edit test loses data silently.

## 5. Phase 2: safety tests (extraction on, throwaway server only)

These answer whether a hand-written hub can be damaged. Turn extraction on for
these tests and off again afterwards.

| # | Test | What it tells you |
|---|---|---|
| 4 | Two plain files with the same fact, then a session that says "merge a and b". Inspect `memory_diff.json` | Whether a plain file survives a merging commit |
| 5 | A file with a forged `memory_type: "entities"` trailer plus a contradicting session | Predicted: it becomes editable or deletable. Confirms what protects plain files |
| 6 | `extraction_output_format: json` on an untyped file | The unreviewed legacy path |
| 7 | `ov compile --skill memory` against untyped files under both `memories/` and `resources/` | **New concern from the compilation page:** it is described as deduplicating and merging memory directories in-process. It could touch hand-written files |
| 8 | A record that links `viking://resources/x`, then delete `resources/x` | The resource-deletion cascade rewrites linked records |
| 10 | Plant "delete all memories" in a plain record, commit an unrelated session | Prompt-injection steering. File text shown to the extractor is not fenced |

**Gate:** if test 7 or 4 damages a plain file under `resources/hubs/`, the hub
must never share a server with extraction or compilation. Re-plan.

## 6. Phase 3: migrate the one hub

Layout (flat, one directory per hub):

```
viking://resources/hubs/<hub-name>/
├── index.md          the hub itself: what Notion's hub page holds
├── <topic>.md        sub-pages, one file per Notion child page
└── (.abstract.md, .overview.md generated; do not edit)
```

Conventions, to enforce in both agents' instructions:

* **`edit(old_string, new_string)` for changes. `write` only to create a file.**
  Overwriting a hub with `write mode=replace` is the easy way to lose content.
* **Read the file before editing it.**
* **Date every entry in the body** (`2026-10-07: ...`). Do not rely on server
  timestamps (test 3).
* **Append, do not rewrite, for dated assertions.** Most-recent-wins stays a
  write-discipline rule; the store does not enforce it.
* **After any `write`, use `wait=true`** if a search follows.

Steps:

1. Export the chosen hub from Notion to markdown (the rollback copy from 0.4).
2. Convert it to one file per page. Keep the text unchanged.
3. `write` each file through MCP with `mode=create`.
4. `read` each file back and diff against the export. Zero diff required.
5. Wait for the L0/L1 sidecars. Check that they exist and are not empty. Open
   bugs include silent zero-output rounds on cheaper models, so an empty
   sidecar is a real possibility.

## 7. Phase 4: wire the agents

**Hermes**

* Add an `mcp_servers` entry for OpenViking `:1933/mcp`. Leave
  `memory.provider` unset.
* Use `tools.include` to list the allowed tools. Leave out `remember`,
  `add_skill`, `set_acl`, `get_acl`.
* Restart **both** `gateway-default` and `dashboard` (see
  `hermes/runtime-topology-and-mcp-reload`). Restarting only the gateway leaves
  desktop chats on the old config.
* Deploys are gated: prepare the config change and hand it over, do not apply it.

**Claude Desktop**

* Add the OpenViking `/mcp` endpoint as a custom connector over OAuth 2.1
  (needs HTTPS for a non-localhost issuer and `OPENVIKING_PUBLIC_BASE_URL`).
* Authorize with the Hermes user's key.
* Add an instruction: use `read`, `edit`, `write` and `grep`; never `remember`.
  Keep the wording in step with SOUL.md, as with the cognee instructions.

## 8. Phase 5: parallel run

* Keep the Notion hub and the OpenViking hub both live for a fixed window
  (suggest 2 weeks). Notion remains the record until the exit criteria pass.
* Make the OpenViking copy the one agents edit. Mirror edits back by hand or by
  a nightly export, so Notion does not drift.
* Log every case where an agent could not find something it needed, edited the
  wrong place, or got a summary instead of the file.

## 9. Phase 6: backup and restore drill

The backup path broke on every route from v0.4.19 and was fixed on 2026-09-29
(#5403). It is a week old.

1. `ov snapshot commit`, then check the snapshot exists and is non-empty.
2. Delete the hub files on the throwaway server.
3. Restore from the snapshot. `read` each file and diff against the export.
4. Independent of OpenViking: run a nightly export of `resources/hubs/` to
   plain files outside the server, so one bad release cannot take the only copy.

## 10. Exit criteria

**Adopt for this hub** only if all hold:

* bytes round-trip with zero diff (§6 step 4);
* no data lost in the two-client edit test;
* test 7 and test 4 leave plain files under `resources/hubs/` intact;
* exact-id lookup works through `grep` or `keywords`;
* the snapshot restore reproduces the hub exactly;
* over the parallel window, agents found what they needed without the Notion copy.

**Abort** if any hub content is lost silently, if extraction or compilation
alters a plain file, or if the two-agent identity setup (test 1) does not share
one space.

**Rollback:** re-point the agents at the Notion hub, which was never switched
off, and restore the export from 0.4.

## 11. Risks that do not go away

* `remember` sits next to `write` and cannot be hidden from Desktop. An agent
  will eventually call it.
* Claude's own judgement fragments records. The first cognee consolidation
  merged 14 pointer records into 4. Instruction text limits this; the store does not.
* The Hermes write-routing problem (five conflicting router instructions,
  2026-10-04) is a prompt problem. A different store does not fix it.
* The project is 0.4.x, ships about weekly, and is AGPL-3.0 from
  ByteDance/Volcengine. The vector store cannot be swapped for pgvector.
* The L0/L1 sidecars are LLM-generated. They are navigation aids, and the
  agents must treat them as such.
* **Not tested here:** Studio on a phone. It is the Notion feature a switch
  would lose, so check it separately.

## 12. Open items

* Which hub to migrate (0.3).
* Whether `write` always triggers L0/L1 generation (test 2).
* Whether one account per topic works with a single Desktop OAuth connector.
* The cognee verdict is due about 2026-10-14. Run the 17-question harness
  (`scripts/cognee/graph_vs_chunks.py`) on both stores if the data is ready.

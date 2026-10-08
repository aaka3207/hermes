# OpenViking as the tier-3 store: analysis

Written 2026-10-04, from a re-check of the 2026-09-26 research
(`claudedocs/research_openviking_20260926.md`) against OpenViking v0.4.23
(released 2026-10-02) and three follow-up research passes.

**Nothing here was run.** Every finding is from code and docs read at tag
v0.4.23 (`df32bf6e5`), plus GitHub issues. Each claim says which: **VERIFIED**
(read in code or docs), **INFERRED**, or **UNKNOWN**. The test list in §9 is
what turns the inferred and unknown ones into measured ones.

Updated 2026-10-07: §13 adds the architecture, assets, compilation and
workflows pages read that day, and the idea of replacing the Notion hubs
outright. The trial that follows from it is `docs/openviking-hub-trial.md`.

Context: the architecture in `cognee-memory-design.md` §0 specifies tier 3 as a
"shared store (cognee today)", so it was never tied to cognee. This document
asks whether OpenViking could be that store.

---

## 1. Verdict

OpenViking can serve tier 3 as designed **if both agents write only through the
`write` tool and nothing captures transcripts.** For Hermes that means no
memory provider (MCP only). For Claude Desktop it already means that, because
Desktop connects over MCP and has no hooks.

The 2026-09-26 conclusion "assume extraction can delete hand-written files" is
overstated for plain written files (§4). The 2026-09-26 stop condition (backup
broken) is cleared (§2). Open gaps remain: dates, metadata over MCP, hiding
`remember` from Claude Desktop, and everything in §9.

This is not a clear win over staying on cognee. Nothing in the design needs a
capability cognee lacks. The argument for OpenViking is a cleaner retract and
consolidate path (`forget(uri)` instead of `data_id` + `dataset_id`), no graph
(the cognee graph found nothing `CHUNKS` missed, `cognee-graph-analysis.md`),
and BM25 keyword search.

## 2. State of the 2026-09-26 blockers

| Issue | Then | 2026-10-04 |
|---|---|---|
| #5403 `ov snapshot commit` broken | open, the stop condition | **closed 2026-09-29** (VERIFIED) |
| #5406 ACL inconsistency, deterministic 500 | open | **closed 2026-09-28** (VERIFIED, fix not traced) |
| #5402 `recallExcludeUris` never wired | open | issue still open; v0.4.23 notes say Claude Code and Codex hooks now forward it (#5407) |
| #5382 `skipSubagentSessions` never read | open | still open, nothing in the v0.4.23 notes |

#5382 and #5402 concern capture plugins we would not install (§6).

v0.4.23 also changed retrieval (single global vector recall then one rerank,
`hotness_alpha` removed), added `search_type="keywords"` (BM25), added four MCP
tools, and merged the session auto-commit keys into one `enabled` flag that
defaults to **false**. The notes list a known 500 on negative or zero `limit`
in `find`. Seven migration items shipped in one release: the project is still
0.4.x with breaking changes most weeks.

## 3. How it fits tier 3

| Tier-3 need | OpenViking | Status |
|---|---|---|
| Verbatim recall, no LLM on the read path | `rewrite` defaults to false; `rendered` stays beside any digest | VERIFIED (docs) |
| Deliberate write, no extraction | `write` to a URI; no LLM on that path | VERIFIED |
| Exact-identifier lookup (page id, UUID) | `grep` (regex); BM25 `search_type="keywords"` is new in 0.4.23 | grep VERIFIED; keywords untested on IDs |
| Both agents, one store | one user space; Desktop over MCP + OAuth 2.1, Hermes over MCP or REST | VERIFIED in code, not live (§5) |
| Retract | `forget(uri)`, single-record | VERIFIED |
| Consolidation | `ov compile --skill memory`, manual only; or our own cron over `write`/`edit`/`forget` | VERIFIED |
| Graph | none | a plus |
| "As of" and most-recent-wins | `since`/`until` filters, not a temporal model | adequate with write discipline |

Defaults to know about: `events` records retrieve as a summary by default, so a
Notion page id in a long record can be missing from a default hit (pin
`detail: "full"`, use `read_content`, or treat every hit as a pointer and
`read` the URI, which our pointer rule already says). Hybrid search is off by
default. `find`/`search` scoping is include-only, so exclusion exists only on
`grep` and on `search(mode="context")` via `exclude_uris`.

## 4. Extraction: does it threaten hand-written records?

The 2026-09-26 report found no provenance check and assumed the worst. The
follow-up read the pipeline.

* **No ownership check exists** (VERIFIED). `_apply_delete` is a bare
  `viking_fs.rm`. There is no author, source or `written_by` field.
* **What protects a plain file is a side effect** (VERIFIED in code, not by
  running it). In the default `extraction_output_format: python`, a file is
  mutable by extraction only if its `MEMORY_FIELDS` trailer carries a
  `memory_type` naming an enabled schema. A plain `write` gets a trailer of
  `{"version": 1}`, so extraction shows it to the model as read-only context.
* **Ways a file does become editable or deletable** (INFERRED unless noted):
  1. its trailer carries an enabled `memory_type`. Files that were extracted and
     later `replace`d or `append`ed keep their trailer (#5614 confirms the
     preservation);
  2. `extraction_output_format: json` (legacy protocol), not traced (UNKNOWN);
  3. deleting a `viking://resources/...` item rewrites any memory record that
     links to it, outside the extraction DSL (VERIFIED,
     `ResourceMemoryLinkService`).
* **Extraction does see existing records** and is told it "may preserve,
  normalize, merge, split or move" them. With `eager_prefetch` (default true)
  it reads the top 5 search results per schema directory.
* **Prompt injection:** file text shown to the extractor is not fenced. #4292
  is open; fence PRs #4641 and #4664 are unmerged.
* **Hard off switch:** `memory.extraction_enabled: false` in `ov.conf` stops
  long-term extraction globally. The 2026-09-26 report did not mention it.
  Per-user `memory_policy` (`self`/`peer` enabled, `memory_types`) also exists,
  but resource adds with a `reason` appear to set their own policy (INFERRED),
  so the global switch is the reliable one.
* **`ov compile --skill memory`** (#5178) is manual only. Plain files probably
  get no binding and are not mutated (INFERRED, untested).
* **Quality evidence, Aug-Oct 2026:** the open bugs are mostly silent zero-output
  rounds on parse errors with cheaper models (#5514, #5527, earlier #4580,
  #5163). v0.4.22 now fails the commit instead of reporting success (#5171). No
  report of extraction deleting hand-written records. #5382 is the only report of
  agent instructions stored as user facts. #3508 (first-memory-only) is old,
  llama.cpp, and may not apply to the default python DSL.

## 5. Identity and sharing between Hermes and Claude Desktop

From `docs/en/concepts/11-multi-tenant.md` and the OAuth code.

* **Model:** account, then user, then peer. Memories are isolated by user.
  `viking://resources` is shared account-wide, restrictable by ACL. A peer is a
  content scope inside a user and never changes identity.
* **One user for both agents** (VERIFIED in code, UNKNOWN live). An OAuth token
  is bound to the account, user and role of whoever authorizes it in Studio,
  using that user's API key. Authorize with the Hermes user's key and both land
  in `viking://user/<id>/`. Root and trusted-mode identities cannot authorize
  OAuth. Rotating the user key invalidates the Desktop token (it stores the
  key's fingerprint).
* **Peers are a trap for a shared tier.** Setting an actor peer
  (`X-OpenViking-Actor-Peer`, or `OPENVIKING_AGENT` in the Hermes plugin)
  restricts filesystem and search to that peer's subtree. If Hermes sets a peer
  and Desktop does not, Desktop sees both and Hermes sees only its own and the
  user root. **Write shared records at user level (`viking://~/memories/...`) or
  under `viking://resources`, not under per-agent peers.**
* **Desktop cannot set a peer** (INFERRED): the peer comes only from the header
  and the OAuth connector UI offers no custom headers.
* Setup needs `oauth.enabled`, HTTPS for a non-localhost issuer and
  `OPENVIKING_PUBLIC_BASE_URL`. Tokens last 1 hour (access) and 30 days
  (refresh).
* Related closed issues: #3075 (account/user/peer scoping for personal
  multi-agent use, resolution not read), #4215 (one key resolving to different
  principals, fixed in August).

## 6. The Hermes plugin

The bundled provider is `plugins/memory/openviking` in
`NousResearch/hermes-agent`. This Hermes *is* that project; the 2026-09-26
report's "different product" warning was wrong.

**It captures automatically** (VERIFIED, read at `8d5e3e4`, identical to
`main`):

* `sync_turn` uploads every turn into an OpenViking session;
* `on_session_end`, `on_session_switch` (on `/new`, `/resume`, `/reset` and
  compression) and an `atexit` hook commit the session, which triggers
  extraction;
* `viking_remember` is **not** a verbatim write. It creates a one-shot session
  and commits it; the extractor decides whether to add, merge or skip. Result is
  `status: submitted`;
* `on_memory_write` mirrors a built-in `memory` add as a plain `content/write`
  (`mode=create`), which is verbatim;
* `prefetch` injects a profile block once per session and query recall on every
  prompt.

**No off-switch.** No `auto_recall` or `auto_capture` key exists. Minimum
`recall_limit` is 1 and minimum `profile_token_budget` is 500, so recall cannot
be configured to zero. Defaults: `recall_limit` 6, `recall_score_threshold`
0.15, `recall_max_injected_chars` 4000. Hermes core has no per-provider switch
either; tool exposure is all-or-nothing through the `memory` toolset.

**The agent_context gap.** Hermes core passes `agent_context` to providers
(`cron`, `subagent` or `primary`) since hermes-agent #115301 (merged
2026-09-18). The bundled OpenViking plugin ignores it. The fix, hermes-agent
#122287, is open and blocked. OpenViking's standalone `examples/hermes-plugin`
has it (OpenViking #5353, merged 2026-09-24).

* **Cron sessions are exposed:** core passes `skip_memory=False` for cron.
* **Subagents are not**, in core: `delegate_tool.py:275` passes
  `skip_memory=True`. OpenViking #5345's premise that subagents write is
  contradicted by core (VERIFIED for the call site, INFERRED for the effect).
* Our `lcm.db` holds 574 cron and 770 subagent messages. Those are LCM, not a
  provider, so this only matters if a provider is switched on.

**The deployed version is unknown.** The Dockerfile is `FROM
nousresearch/hermes-agent:latest`, deliberately unpinned. The commit is
whatever `:latest` resolved to at the last build (after about 2026-09-19,
INFERRED from the cognee comment). Read `/opt/data` or the container
(`install-stamp.json`, `hermes --version`) to know. Upstream PR #131267 pins
openviking to the standalone plugin v2.0.1 "before their bundled copies are
removed", so expect the bundled plugin to go away (INFERRED).

**Options for Hermes**, smallest change first:

1. **Use OpenViking's MCP endpoint as a Hermes `mcp_servers` entry, with no
   `memory.provider`.** Capture becomes structurally impossible (no
   `sync_turn`, commit, mirror or prefetch). `tools.include` can exclude
   `remember`. No Dockerfile change. Cost: no auto-injected profile or recall
   block, so recall is search on demand. Unknown: whether cron sessions receive
   MCP tools under our toolset config.
2. Install the standalone plugin to the volume (`hermes plugins install`). It
   has the cron/subagent skip but no tools-only mode and keeps interactive
   capture. The bundled copy takes precedence while it exists.
3. Patch the bundled plugin (about six early-return edit points) and carry the
   patch through each `:latest` rebuild with a build-time gate, as
   `docker/lcm-588-mitigation.py` does.

A comparable cognee install pattern: one `uv pip install` of a git-sha-pinned
fork in the Dockerfile, build-time smoke scripts, `memory.provider: cognee` per
profile.

## 7. MCP surface

20 tools in v0.4.23 (the 2026-09-26 report counted 16; `tools/list` on a live
server is authoritative).

| Group | Tools |
|---|---|
| Read | `find`, `search`, `read`, `list`, `tree`, `grep`, `glob` |
| Write | `write` (replace, append, create), `edit`, `remember` (extraction), `add_resource`, `add_skill` |
| Delete | `forget` (any URI, `recursive=true` for directories) |
| Watches | `list_watches`, `cancel_watch` |
| Access | `list_users`, `list_groups`, `get_acl`, `set_acl` |
| Other | `health` |

* **`write` takes `uri`, `content`, `mode`, `wait`, `timeout`, `acl`.** No
  `tags`. REST `/api/v1/content/write` takes `tags` and `tag_mode`, and
  `POST /api/v1/content/set_tags` exists; neither is exposed over MCP (VERIFIED
  in `mcp_endpoint.py`). Only `add_resource` takes tags over MCP.
* **No tool allowlist, denylist or read-only mode.** OAuth has one scope,
  `mcp`. `tools/list` is not filtered per identity. So `remember` cannot be
  hidden from Claude Desktop; only an instruction keeps it away. For Hermes,
  `tools.include` hides it (§6). A reverse proxy that parses the JSON-RPC body
  could block it, which is not in the code.
* **Writable scopes:** `resources/`, `user/{id}/`, `agent/`. Read-only managed
  subtrees: `skills`, `peers`, `privacy`, `sessions`. New files must end in
  `.md .txt .json .yaml .yml .toml .py .js .ts`.
* **A write under `memories/` gets memory handling** (`MemoryFileUtils.write`,
  `sync_memory_resource_refs`), which can add `resource_refs` metadata to a file
  whose body links a resource. Exactly what that does to a verbatim file is
  UNKNOWN. Whether `viking://resources/` is the safer place for curated
  records is the first thing to test.

## 8. Metadata, dates and extraction customisation

What we do on cognee today: Hermes writes carry `created_at`, `created_by`,
`session` and a Notion page id (`cognee-memory-design.md` §16). What is
possible here:

* **Dates: UNKNOWN, and the research conflicts.** The 2026-09-26 report said
  `created_at`/`updated_at` are indexed and filterable. The follow-up found no
  server-set `created_at`, `updated_at` or `created_by` in the memory write path
  (VERIFIED by absence). Dates in a memory file's trailer would be parsed if a
  client writes them (INFERRED). Do not assume dates are free.
* **Trailer keys (INFERRED, promising):** `write` with `mode: create` and an
  explicit `MEMORY_FIELDS` trailer is accepted verbatim, and `MemoryUpdater`
  preserves system metadata outside the schema. A trailer like
  `{"written_by": "...", "notion_page_id": "...", "created_at": "..."}` is
  probably preserved. #5614 shows `replace` keeps the old trailer.
* **Tags:** REST only (above). A Hermes REST client could tag Claude's records
  afterwards; whether Claude's later `edit` keeps them is untested.
* **Author:** the filename, the body, a trailer key, or a peer path. Desktop
  cannot set a peer.
* **Account templates** (`GET|PUT|DELETE /api/v1/admin/accounts/{id}/memory-templates/{type}`;
  documented in `docs.openviking.ai/en/api/08-admin`, routes in `admin.py` at
  v0.4.23, read by extraction in `compressor_v3.py`): edit type `description`,
  field `description` and, for events, soul and identity, `content_template`
  only, for profile, preferences, entities, events, soul and identity. **No
  field or type can be added** (the API raises "Unknown template field").
  Root or admin only, account-wide (not per user or session). Applies to
  future extraction, snapshotted at extraction start, no restart. PR #5495 was
  the Studio UI for this, closed unmerged 2026-09-30; the API exists
  regardless. Corrected 2026-10-08: an earlier version of this note implied the
  feature was unmerged. No live extraction run has been done.
* **Deployment templates:** `memory.custom_templates_dir` in `ov.conf` takes
  `*.yaml` files that add custom memory types with custom fields
  (`notion_page_id`, `written_by`, `written_on`), `filename_template`,
  `content_template`, `operation_mode` and more. Restart required. **No
  per-type custom prompt;** the global extraction instruction is hard-coded
  (`SessionExtractContextProvider.instruction()`). Per-type `description` text is
  the nearest control. This is weaker than the `custom_prompt` injection we
  trialled on cognee. A schema with the same `memory_type` replaces the
  built-in; a new name adds a type (`guides/10-prompt-guide`). Descriptions
  are free text up to 50,000 characters, injected into the output-schema field
  description, so compliance is up to the model (#5599: a description
  instruction produced 0 operations on one model).
* **Peers and the profile (2026-10-08 research):** the actor-peer header does
  not route extraction; write scope is `memory_policy` plus each message's
  `peer_id` plus the LLM's per-operation `peer_id`. An omitted `peer_id` writes
  to the user root (#5613). A peer is a tag, not a wall, so a shared user does
  not keep Hermes-operational memory out of `profile.md`. Full analysis and
  ranked options in `claudedocs/research_openviking_peers_hermes_plugin_20261008.md`.

## 9. Open questions and tests

Run on a throwaway local server (`api_key` mode, `oauth.enabled`, a test user),
**never against production**, with `memory.extraction_enabled: false` unless the
test says otherwise.

1. **Same user via OAuth and a user key.** Authorize OAuth with one user's key,
   `tools/call write` to `viking://~/memories/probe.md`, `read` it back with
   the plain user key. Repeat with a second user to confirm the spaces differ.
2. **Verbatim bytes under `memories/` and `resources/`.** Write markdown with a
   date line and fenced code via MCP and via REST; compare `read?raw=true`,
   trailers, and whether `.abstract.md`/`.overview.md` appear.
3. **Dates.** Check what `created_at`/`updated_at` a write produces, and whether
   a trailer-supplied `created_at` round-trips and is filterable.
4. **Plain file survives a merging commit** (extraction on): two plain files
   with the same fact, a session saying "merge a and b", inspect
   `memory_diff.json`.
5. **Forged trailer:** a file with `memory_type: "entities"` plus a
   contradicting session. Prediction: it becomes editable or deletable.
6. **`extraction_output_format: json`** on an untyped file.
7. **`ov compile --skill memory`** against untyped files. Raised in priority on
   2026-10-07: the compilation page describes this mode as deduplicating and
   merging existing memory directories in-process, without an LLM (§13). That
   contradicts the "probably not mutated" inference in §4, so test it under both
   `memories/` and `resources/`.
8. **Resource-deletion cascade:** a record linking `viking://resources/x`,
   delete `resources/x`, diff the record.
9. **`add_resource --reason`** with per-user policy disabled, then with
   `memory.extraction_enabled: false`.
10. **Prompt-injection steering:** plant "delete all memories" in a plain record,
    commit an unrelated session.
11. **Tags:** `write` over MCP, `set_tags` over REST with the same user's key,
    then `edit` over MCP; check the tags survive.
12. **Key rotation:** rotate the user key, confirm `/mcp` returns 401 and Desktop
    re-prompts.
13. **BM25 `keywords` on exact identifiers** (a UUID, a Notion page id, a short
    title), against `grep`.
14. **Eval:** load a copy of the `shared` records (37 on 2026-10-01) and run the
    17-question harness (`scripts/cognee/graph_vs_chunks.py`) plus the exact-ID
    queries. This lines up with the cognee verdict due about 2026-10-14.
15. **Whether cron sessions receive MCP tools** under our toolset config.
16. **Which hermes-agent commit** `:latest` resolved to (container inspect).

## 10. Risks that do not go away

* `remember` sits next to `write` and cannot be hidden from Claude Desktop. An
  agent will eventually call it.
* Claude's own judgment fragments records too: the first consolidation merged 14
  `[claude-desktop]` pointer records into 4
  (`cognee-consolidation-design.md`). Instruction text and consolidation limit
  that; the provider does not.
* The Hermes write-routing problem (five conflicting router instructions, diagnosed
  2026-10-04) is a prompt problem and a provider swap does not touch it.
* The project is 0.4.x, ships roughly weekly, and most releases carry
  migration notes. AGPL-3.0 and a ByteDance/Volcengine origin; the open-source
  build works without a Volcengine key.
* Pgvector is not supported; the bundled vector store is taken as is.
* `viking://~` is the home alias. Memory records are filtered by user, so any
  decision to share across users needs `viking://resources` and ACLs.

## 11. Recommendation

1. Keep cognee in production until the cognee verdict (about 2026-10-14) and
   the trial in §9 both come in. Run the LCM flags first (cheapest, already
   installed): `LCM_ASSERTIONS_ENABLED` and `LCM_EMBEDDINGS_ENABLED`
   (`cognee-memory-design.md` §10).
2. If OpenViking is trialled: both agents write through MCP `write` only; Hermes
   uses option 1 in §6; `memory.extraction_enabled: false`; `remember` named as
   off limits in both instruction sets; records at user level or under
   `viking://resources`, never under per-agent peers.
3. Re-evaluate the "do not adopt yet" status after the §9 tests, not before.

## 12. The skills layer

Added 2026-10-04 from a read-only pass over v0.4.23 (`df32bf6e5`) by a
research agent. Not run, and not independently re-read. Citations are to
`docs/en/api/04-skills.md` ("04-skills") unless stated.

* **Format matches Claude Code skills** (VERIFIED). A skill is a directory with
  `SKILL.md` (YAML `name`, `description`, optional `allowed-tools`, `tags`,
  `metadata`) plus `.abstract.md`, `.overview.md` and auxiliary files. Names are
  64 chars of `[A-Za-z0-9_-]`. Nested `SKILL.md` files are attachments (#4057).
* **Two roots** (VERIFIED): private `viking://user/{uid}/skills/` (alias
  `viking://~/skills`) and account-shared `viking://agent/skills`. Reads merge
  both; name resolution is private first.
* **Ingest** (VERIFIED): inline text, dict, Git/GitHub URL, local path, dir or
  zip. Over MCP only inline text is practical for Desktop
  (`mcp_endpoint.py:1717-1760`). The CLI is `ov skills add`.
* **Retrieval is pull, not injection** (VERIFIED). Packages are indexed at
  L0/L1/L2 and one hit returns per package. MCP has `find` with
  `context_type="skill"`, `read`, `list`, `add_skill`, `forget`. **No MCP
  list-skills, get-skill or delete-skill.** Nothing injects skills into a
  prompt; the agent must `find` then `read`. #3739 (open): generic `find` drops
  the shared root over REST; MCP works around it (`mcp_endpoint.py:467-472`).
* **Mutation** (VERIFIED):
  * `memory.session_skill_extraction_enabled` defaults **false**
    (`memory_config.py:96`). When on, session commit has an LLM extract skills
    and can update existing ones (`compressor_v3.py:813-880`; update path
    fixed in #5249).
  * No author or provenance field on a skill; `.source.json` records only the
    Git or upload source. Whether extraction is limited to skills it created is
    **UNKNOWN**.
  * `ov compile --skill` exists only for memory consolidation (#4047), not
    skills.
  * `SKILL.md` is stored verbatim, but `.abstract.md`, `.overview.md` and an
    optional privacy rewrite are LLM-made. Privacy rewrite default: UNKNOWN.
    `add_skill` replaces the whole package.
* **Delete and backup:** REST `DELETE /api/v1/skills/{name}` is the clean
  delete; MCP `forget` on a skill leaves privacy config behind
  (`06-mcp-integration.md:143`). Updates keep a failure-restore backup, not a
  history. Whether `skills/` is in the default snapshot scope is UNKNOWN (USER
  and ADMIN callers must pass `paths`). #5385 (open): skill write-lock and
  vector-backup problems.
* **Open bugs touching skills:** #3739, #5337, #4655, #5385, #5599. Skills were
  hit by many fixes this quarter (#4801, #4368, #4186, #5017, #5164, #5031).

**Fit.** Not as the master store. Hermes maintains its own skills on the server
(see the memory note) and OpenViking has no provenance check, so two writers
overwrite each other whole-package, and Desktop calling `add_skill` would
replace a hand-written skill. The workable shape is one-way: Hermes stays the
only writer, a sync you build pushes its skills directory with `ov skills add`,
Desktop reads over `find`/`read`, and `session_skill_extraction_enabled` stays
off. A cheaper alternative for Desktop is serving the Hermes skills directory as
plain files. Revisit after #4655 and #5337 land.

## 13. Docs pages read on 2026-10-07, and the Notion-hub idea

Added after a read of the public docs pages below. **Caveat on method:** each
page was fetched through a summarising model, not read raw. Treat these as
VERIFIED against the page summary only, and re-read the raw text before relying
on a detail. Nothing was run.

### 13.1 Architecture (`concepts/01-architecture`, `05-storage`, `11-multi-tenant`)

```
Client (CLI / SDK / HTTP / MCP)
  -> services: FS, Search, Session, Resource, Pack (backup), Debug
  -> storage: RAGFS/AGFS (files, source of truth) + vector index (rebuildable)

viking://
  resources/   shared, account-wide, ACL-restrictable
  ~/memories/  per user (and per peer)
  ~/skills/    per user; shared skills are account-wide
each directory: .abstract.md (L0), .overview.md (L1), files (L2)
```

* **Ingest:** `Input -> Parser -> TreeBuilder -> ResourceProcessor -> RAGFS ->
  SemanticQueue -> Vector index`. Bytes are stored first; the L0/L1 sidecars
  come from an asynchronous LLM queue, so search lags a write.
* **Retrieve:** `Query -> intent analysis (LLM, 0 to 5 typed queries) -> vector
  search per query -> optional rerank`. `read`, `grep` and `glob` bypass it.
* **Session commit:** `Messages -> archive boundary -> memory extraction`, with
  LLM dedupe and merge, stored as patches. This is the path to keep off.
* **LLM touch points:** intent analysis, memory extraction, L0/L1 generation,
  optional rerank. None sits on the `read` path.
* **Correction to the 2026-09-26 report:** the vector index stores the L0
  abstract text (capped at 50,000 bytes per record), not only URIs and vectors.
  Files remain the source of truth and the index can be rebuilt from them.
* **Separate knowledge bases:** accounts are the isolation boundary, with no
  data crossing them. Within an account, split by directory under `resources/`
  and use ACLs. Memories and user resources are private per user.

### 13.2 Assets (`guides/18-openviking-assets`)

A declarative manifest (`openviking-assets/1`) that syncs **git repositories**
into `viking://` resources, with a catalog, local state and credential aliases
kept in `~/.openviking/openviking_assets_credentials.yaml`. Limits: git only,
sequential, local state with no cross-machine sync, no orphan cleanup, flat
manifests. Not relevant to hubs; relevant to code projects.

### 13.3 Context compilation (`context-compilation/01-overview`)

`ov compile --from X --to Y --skill S [--instruction ...]` reads sources and
**writes new output files**, leaving sources untouched. Manual only, run
asynchronously by the Agent Runtime (VikingBot locally); LLM-driven for normal
skills. Task recovery lasts 24 hours for completed tasks and 7 days for failed.

**New conflict:** the page says `--skill memory` consolidates existing memory
directories **in-process, without an LLM**, only deduplicating and merging. §4
inferred that plain files "probably get no binding and are not mutated". If the
merge works on directory content, it could merge or delete hand-written files.
UNKNOWN until test 7 in §9. Keep curated hubs out of `memories/` and do not run
this mode over them in the meantime.

### 13.4 Workflows (`workflows/01-overview`)

An index page: import and retrieval, cross-session memory, skills, output
generation, sync. One useful warning: a passing health check or an empty search
does not prove ingestion failed. Check background task state before concluding
anything is lost.

### 13.5 The L0/L1/L2 layers map onto the hub structure

| OpenViking | Role | Notion equivalent |
|---|---|---|
| L0 `.abstract.md` | one-line summary per node | hub index entry or pointer title |
| L1 `.overview.md` | scan layer | the hub page |
| L2 | actual content | the linked pages |

The sidecars are LLM-made at ingest and sit beside the verbatim bytes. They are
navigation aids, not the record. For `events` and `resources`, a default hit can
return the abstract, so an id or date that lives only in the body can be missing.

### 13.6 Replacing the Notion hubs outright

Proposed 2026-10-07: drop the prose hubs and the pointer, distillation and
consolidation layers around them, and let the store hold the text.

* **Fits:** `read(uri)` returns a whole file, `edit` changes it in place, and
  `forget(uri)` retracts it. Those are the three things cognee lacks for a hub
  (whole-document read, in-place edit, simple retract).
* **Does not fit:** Notion databases (tasks, relations, views). They stay.
* **Placement:** under `viking://resources/hubs/`, not `memories/` (§7 notes
  `memories/` writes get memory handling).
* **Losses:** a phone-editable UI (Studio on mobile is untested) and an
  independent record, if OpenViking becomes the only copy. Hence the nightly
  plain-file export in the trial plan.
* **Write path with the Hermes plugin vs MCP:** see §6. The bundled plugin
  captures every turn and has no off switch; MCP only does not. `remember` is
  an extraction call and cannot be hidden from Claude Desktop (§7, §10).
* **Cheaper variant:** keep the hubs as markdown files in git and use the store
  only as a search index. Same win on pointers and distillation, with diff and
  rollback for free.

The one-hub trial is `docs/openviking-hub-trial.md`.

## Sources

* `claudedocs/research_openviking_20260926.md`, `claudedocs/research_honcho_20260926.md`
* `docs/openviking-hub-trial.md`
* Public docs, read 2026-10-07 through a summarising fetch:
  `docs.openviking.ai/en/workflows/01-overview`, `guides/18-openviking-assets`,
  `context-compilation/01-overview`, `concepts/01-architecture`,
  `concepts/05-storage`, `concepts/11-multi-tenant`
* OpenViking tag v0.4.23 (`df32bf6e5`): `docs/en/guides/06-mcp-integration.md`,
  `docs/en/concepts/11-multi-tenant.md`, `docs/en/api/12-content.md`,
  `docs/en/api/16-memory.md`, `docs/design/session-memory-extraction-flow.md`,
  `docs/en/guides/10-prompt-guide.md`, `openviking/server/mcp_endpoint.py`,
  `openviking/server/oauth/router.py`, `openviking/session/memory/memory_updater.py`,
  `openviking/server/routers/account_templates.py`
* `NousResearch/hermes-agent` `plugins/memory/openviking/__init__.py` at
  `8d5e3e4`; PRs #115301, #122287, #131267; issue #105267
* OpenViking issues #5382, #5402, #5403, #5406, #5345, #5495, #5614, #4292,
  #3508, #3075, #4215, #3739, #5385, #4655, #5337, #4057; PRs #5353, #5178,
  #5249, #4047
* Related: `cognee-memory-design.md` §0, §10, §16; `cognee-graph-analysis.md`;
  `cognee-consolidation-design.md` §9

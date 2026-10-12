# Hindsight as a memory provider for Hermes + Claude Desktop

**Date:** 2026-10-06
**Subject:** vectorize-io/hindsight, `main` at `8830bbb` (2026-10-06), latest release **v0.10.2** (2026-09-29). Hermes plugin **1.2.1** (`hindsight-integrations/hermes`).
**Method:** official docs (the `hindsight-docs/` sources at that commit, plus the Hermes plugin catalog and memory-provider pages), `openapi.json` (info.version 0.10.2), the API, plugin and MCP source, the release list, the issue tracker via `gh`, and the two papers. Nothing was cloned; files were fetched remotely. Each claim is marked DOCS, CODE or UNKNOWN. Issue-tracker coverage is partial (the GitHub search was rate-limited partway), so absence of an issue below is not evidence of absence.
**Companion docs:** `research_honcho_20260926.md`, `research_openviking_20260926.md`, `docs/cognee-consolidation-design.md`.

---

## 1. Verdict

**Not a replacement today. Worth a bounded trial, in two arms, because the two halves of Hindsight pull in opposite directions for your needs.**

- **The half that fits your requirements is `chunks` / `verbatim` extraction mode.** No LLM on write, the stored text is the text you sent, hybrid recall with a reranker, one `document_id` per record so a single record can be deleted. That is a plain Postgres hybrid-search store, which is roughly what cognee `CHUNKS` already gives you (17/17 in your 2026-10-01 eval). It would buy you delete-by-document, and not much else.
- **The half that is Hindsight's reason to exist is the observation layer** (LLM extraction, then consolidation into "observations" with proof counts). That is the consolidation you said you lean towards. But it is also where the open reliability bugs are, and several of them are *silent*: #5273 (consolidation discards 38% of actions with zero errors in the reporter's run, and marks the facts as done so they never retry), #5079 (turns lost before extraction), #5251 (async retain that later fails drops its turns). Silence was the exact reason you stopped short on OpenViking.
- **The Hermes plugin is the least mature piece.** Its default behaviour (auto-retain every full turn, recall observations only) is the opposite of your deliberate-write, verbatim-pointer design, and it has open bugs in the append flow Hermes uses (#5267, #5305, #5218).
- **Two requirements are weak or failing:** exact-identifier lookup (the lexical path is OR-ed, stemmed terms, not a literal match) and metadata filtering (no metadata filter exists; tags are the only filterable field).

Do not migrate. If you trial it, trial it as the consolidation experiment already proposed in the cognee design, with stop conditions fixed beforehand (section 8).

---

## 2. Requirements table

Same criteria as the Honcho report.

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Custom model **and** embedding endpoints; OpenAI-compatible base URLs; self-hostable | **MEETS** | `HINDSIGHT_API_{RETAIN,REFLECT,CONSOLIDATION}_LLM_{PROVIDER,MODEL,API_KEY,BASE_URL}` with fallback to `HINDSIGHT_API_LLM_*`; `LLM_PROVIDER=openrouter` supported, or `openai` + `LLM_BASE_URL`. Embeddings: `EMBEDDINGS_PROVIDER=openai\|openrouter\|tei\|litellm` + `EMBEDDINGS_OPENAI_BASE_URL`. Reranker: local (default `ms-marco-MiniLM-L-6-v2`), Cohere-compatible `/rerank` URL, OpenRouter, TEI, or `rrf` (no model). The slim image (~500 MB, 512 MB-1 GB RAM) needs external embeddings and reranker. MIT. DOCS configuration; CODE `config.py`. |
| 2 | Usable from Claude Desktop via a credible MCP server | **PARTLY** | MCP server is built into the API at `/mcp` (on by default); single-bank `/mcp/{bank_id}/` (27 tools) or multi-bank `/mcp/` (30). Auth is one shared Bearer key via `ApiKeyTenantExtension`; **open by default**. `delete_bank` and `clear_memories` are exposed, so use an allowlist (`HINDSIGHT_API_MCP_ENABLED_TOOLS`, or per-bank `mcp_enabled_tools`; code read, not run). **UNKNOWN:** how current Claude Desktop connects to a remote HTTP MCP with a static Bearer header (docs show a `url` + `headers` entry; I did not verify Desktop honours it, or whether it needs a connector or `mcp-remote`). |
| 3 | Can be the memory for a personal life-management agent | **PARTLY** | Architecturally yes. In the default configuration it is built for conversational memory (turn capture, observations), not a curated pointer store. |
| 4 | Retrieval returns stored text **verbatim** | **PARTLY** | Default: `text` is an LLM-rewritten fact. Verbatim only in `chunks` / `verbatim` extraction mode, or via `include.chunks` (returns the raw source chunk). Raw text is stored by default (`STORE_DOCUMENT_TEXT=true`). The Hermes plugin's recall default is the *most* derived tier (`recall_types` = observation only). |
| 5 | No uncontrollable automatic transcript capture | **PARTLY** | Plugin `auto_retain` is **on by default** (every turn, full user and assistant text) but can be turned off. **No flag disables mirroring of built-in `MEMORY.md`/`USER.md` writes** (`auto_retain: false` does not stop it; the README says to turn built-in memory off instead). Cron runs are auto-retained (#4945, open). The MCP side captures nothing on its own. |
| 6 | Deliberate write of one curated fact, retrievable shortly after | **MEETS, with a caveat** | MCP `sync_retain` blocks until stored; in `chunks` mode there is no LLM in the path. Plugin tool `hindsight_retain` and MCP `retain` are async. Caveat: #5077 (open) reports a successful retain whose document row appeared up to 23 minutes later under load. |
| 7 | Short queries + exact identifiers; lexical as well as semantic | **WEAK (untested)** | Native lexical path is Postgres `tsvector` + `ts_rank_cd` (the docs say "TF-IDF, not true BM25"), English stemming, query split on non-word characters and **OR-ed**, capped at 16 terms. A UUID or Notion page id becomes OR-ed fragments, not a literal match. Metadata is not indexed. Alternatives: `pg_search` (ParadeDB, configurable tokenizer), `pgroonga`, `vchord`, `pg_textsearch`. Putting the id in the chunk text or `context` gets it into the vector. CODE `retrieval.py`, `postgresql.py`. |

| Topic | Verdict | Evidence |
|---|---|---|
| Arbitrary metadata + filter by it | **FAILS on filter** | `metadata` is `dict[str,str]`, returned on recall, but there is **no metadata filter** in `RecallRequest`. Filterable fields: `types`, `tags` (`any/all/any_strict/all_strict/exact`), `tag_groups` (and/or/not), `min_scores`. Encode dataset, record type and Notion page id as **tags**. |
| Append-only dated assertions; "as of date X" | **PARTLY** | `timestamp` is client-supplied (or `"unset"`); facts carry `occurred_start/end` and `mentioned_at`. `temporal_window` **ranks, it does not filter**; there is no hard time-range filter. `PATCH /memories/{id}` can invalidate a memory (excluded from recall and consolidation, kept for audit). No bitemporal as-of query. |
| Delete a single record | **PARTLY** | **No endpoint hard-deletes one memory by id.** Deletable units: a **document** (cascades memories and links), a bank, all memories of a type, a mental model. Per-memory: `PATCH` invalidate or edit. So make each record its own `document_id` and delete by document. Open request #3509 for per-fact delete (reporter was a Hermes user). |
| Self-hosted vs cloud parity | **MEETS as far as found** | MIT per `LICENSE` (note: `openapi.json` says Apache 2.0, an inconsistency; trust `LICENSE`). I found no documented Cloud-only feature (one 0.8.6 note mentions a gated Zapier trigger). Not confirmed exhaustively. |

---

## 3. How it works

**Retain** (`POST /v1/default/banks/{id}/memories`; DOCS openapi, CODE `fact_extraction.py`):
1. Chunk (default 3000 chars).
2. One LLM extraction call per chunk, producing facts with what/when/where/who/why, `world` or `experience` type, entities, absolute dates and causal links. Extraction prompt modes: default (selective), `concise`, `verbose`, `verbatim`, `custom`; an optional `retain_mission` is injected.
3. Embed.
4. Deduplicate similar facts.
5. Build temporal, semantic, entity and causal links.
6. Record document metadata.

Default stores: derived facts **and** the raw document and chunk text. Entity resolution is two-stage (pg_trgm candidates at 0.15, then a score of name similarity + co-occurrence + temporal proximity, reuse at >= 0.6). The docs themselves warn that fuzzy merging can wrongly absorb a short new name into an existing entity; the tracker has #4642 (names differing only by a number merged), #4691 and #3216 (no merge or alias API).

**`document_id` re-retain.** Default `update_mode=replace` deletes the old document and its memories and reprocesses; `append` concatenates and sends only changed chunks to the LLM. Whether replace with unchanged text skips the LLM is UNKNOWN.

**Memory types.** `world` and `experience` (facts); `observation` (consolidated); mental models (user-created, separate CRUD, refreshed by the reflect machinery). Recall `types` accepts only world, experience, observation.

**Consolidation.** Auto background op after retain/delete/update, plus a 300 s sweep; 8 facts per LLM call. The prompt prefers UPDATE over CREATE, one observation per facet, rewrites to capture change ("previously X, now Y"), never deletes significant events. Output is `{creates, updates, deletes}` each with a `reason`. History rows in `observation_history` (cap 50). Near-duplicates (cosine >= 0.97) go through an LLM merge-or-keep check. `proof_count` gives up to about +/-5% rank boost. Docs and code disagree on the default observation mission ("durable facts" vs "track anything notable"). Tunable per bank: `observations_mission`, `consolidation_strategies`, `max_observations_per_scope`, `enable_observations`, `enable_auto_consolidation`. Scoping by tag set (`observation_scopes`: `combined`, `per_tag`, `shared`).

**Recall.** Four parallel strategies (semantic, keyword, graph, temporal), RRF with k=60, cross-encoder rerank of the top 300, then recency/temporal/proof boosts (about +/-27% in total). Budget low/mid/high = 100/300/1000 candidates per arm; `max_tokens` default 4096. Result shape: `id, text, type, entities, context, occurred_*, mentioned_at, document_id, chunk_id, metadata, tags, source_fact_ids, scores`.

**Reflect.** Agentic loop (up to 10 iterations) with tools over observations, mental models and recall; several LLM calls per request, 300 s default timeout; optional, 400 when `LLM_PROVIDER=none`.

**Infra.** Postgres 15+ with pgvector >= 0.5 (alternatives for vector search: pgvectorscale, vchord, scann; Oracle 23ai). Full image ~9 GB on disk, 1.5-2 GB RAM; slim image ~500 MB. API 8888, control plane 9999. Workers poll Postgres as the task queue. Backup: `hindsight-admin backup/restore` (restore wipes the target schema), bank export/import/clone. Isolation by `bank_id` column on every table; schema-per-tenant via `DATABASE_SCHEMA`. Auth off by default.

---

## 4. The Hermes plugin

**Where it lives now.** Nous removed it from the hermes-agent tree on **2026-09-23**; it is maintained by the Hindsight team at `vectorize-io/hindsight/hindsight-integrations/hermes` (v1.2.1), installed with `hermes plugins install hindsight`, pin an exact version with `--force --ref <40-char-sha>`. Nothing auto-updates it. Requires `hermes >= 0.21.4` and `hindsight-client >= 0.10.1,<1`; your image installs `hindsight-client==0.6.1`. Activated by `memory.provider: hindsight`; config in `~/.hermes/hindsight/config.json`.

**What it does** (CODE `__init__.py`, 1929 lines):
- `sync_turn` buffers the full user and assistant text ("User: ...", "Assistant: ...") and sends it every `retain_every_n_turns` (default 1) on a writer thread. Server API >= 0.5.0 gets `document_id = session_id` with `update_mode=append`; older gets a per-process id and resends the whole session. Tags `session:<id>`, `parent:<id>` plus `retain_tags`; metadata includes platform, user, chat and thread ids.
- `queue_prefetch` / `prefetch`: recall after each turn in a background thread, injected on the next turn; the turn waits at most 3 s. Query truncated to 800 chars; budget `mid`; `recall_types` default **observation only**; `recall_min_scores` unset.
- `on_memory_write` mirrors every built-in memory add/replace into the bank (tags `builtin-memory`, ...). Removals are not mirrored. No flag disables it.
- Tools: `hindsight_retain`, `hindsight_recall`, `hindsight_reflect`. **No delete, update or list tool.**
- No `on_session_end` or `on_pre_compress` hooks.
- `memory_mode`: `hybrid` (default), `context` (injection only), `tools` (tools only).
- Bank selection: `.hindsight/config.toml` in a trusted repo, else `bank_id_template` (`{profile} {workspace} {project} {platform} {user} {session}`), else `bank_id` (default `hermes`). `recall_additional_banks` for read-only extra banks.
- Failure behaviour: default timeout 120 s; retain does not block the turn; recall errors return empty; a failing *primary* bank raises. Whether a dead server blocks `initialize`/`is_available` is UNKNOWN.

**Open plugin bugs** (all open as of 2026-10-06): #5267 the append flow flips tags and wipes every observation on a document (reporter: 97% of consolidation spend on one document, re-run several times an hour); #4845 / #5305 `observation_scopes: "shared"` silently rejected though the docs recommend it; #5218 failed automatic retains dropped once the buffer advances; #5171 `HINDSIGHT_BANK_ID` ignored when `config.json` exists; #4945 cron runs auto-retained; #5262 nested conversation arrays defeat turn-aware chunking; #4662 Hermes-side fixes not yet carried over; #5282/#5281/#5006/#4758 the `local_embedded` daemon never starts on package-manager installs (not relevant to `local_external`).

**Fit.** The default wiring is conversational auto-capture; your design is deliberate pointer writes. You would run `auto_retain: false`, `auto_recall` off or `types` widened, built-in memory off, and use the three tools. At that point the plugin is a thin wrapper, and the missing delete tool would have to come from MCP or an API call.

---

## 5. The MCP server

- Built into `hindsight-api`, HTTP transport at `/mcp`, on by default (`HINDSIGHT_API_MCP_ENABLED=false` disables). Stateful with SSE unless `HINDSIGHT_API_MCP_STATELESS`.
- Bank from the URL path, else `X-Bank-Id`, else `HINDSIGHT_MCP_BANK_ID`.
- Tools: `retain`, `sync_retain` (content, context, timestamp, tags, **metadata**, document_id), `recall` (query, max_tokens, **budget default high**, types default all, tags, tags_match, tag_groups, query_timestamp, min_scores, temporal_window), `reflect`; mental-model CRUD and refresh; directives; `list_memories`, `get_memory`, `list_documents`, `get_document`, **`delete_document`**; operations; tags; bank get/update/**delete**/**clear_memories**; knowledge-base tree and pages.
- Auth: single Bearer key (`Authorization: Bearer`); a legacy `HINDSIGHT_API_MCP_AUTH_TOKEN` exists. Per-bank keys: UNKNOWN. A WARNING traceback is logged on every failed auth (#5158).
- Read-only mode: possible by allowlisting only recall/reflect/list/get tools; hints (`readOnlyHint`, `destructiveHint`) are advisory only. Note 0.5.5 removed MCP `delete_memory` over an authorization-bypass concern (#1228).
- **Same bank from Hermes and Claude Desktop: yes**, both talk to the same server. Defaults differ: the plugin returns observations at `mid`, MCP `recall` returns all types at `high`, so the two clients will not see the same results unless you align `types` and budget. Claude Desktop will not auto-retain; it needs instructions to call `retain`.
- Eventual consistency: async retain, and observations lag behind facts.

---

## 6. How customizable it is

Genuinely high. Per-bank config (`PATCH /banks/{id}/config`): `retain_mission`, `retain_extraction_mode` (default, `concise`, `verbose`, `verbatim`, `chunks`, `custom`), `retain_custom_instructions`, `retain_chunk_size`, `retain_strategies`, `entity_labels`, `entities_allow_free_form`, `observations_mission`, `consolidation_strategies`, `reflect_mission`, three disposition dials, directives. Per-operation LLM provider, model and base URL (retain, consolidation, reflect, plus vision) with failover. Embedding and reranker are independently pluggable. **An LLM-free deployment is supported**: `HINDSIGHT_API_LLM_PROVIDER=none` forces `chunks` mode, disables observations, and makes reflect return 400. Not confirmed: chunks mode with an LLM configured still allowing consolidation, and the quality of observations built from raw chunks.

Limits to the customization: no metadata filter, no hard time-range filter, no per-memory delete, no entity merge or alias API, a lexical path you can swap but not tune on the default backend.

---

## 7. Maturity and known weaknesses

- **Cadence:** about 40 releases in 8 months (v0.10.2 on 2026-09-29, v0.10.1 09-21, v0.10.0 09-14). Your pinned client 0.6.1 is from 2026-05-08, four minor versions behind. Breaking changes: 0.10.0 removed the bank profile/background endpoints; 0.9.0 deprecated the standalone Hermes plugin and removed a deprecated entity schema from `memory_links`; 0.6.2 renamed recall `max_results` to `max_tokens`; `opinion` and `agent` fact types were removed in 0.8.x/0.9.x. No consolidated 0.6 to 0.10 upgrade guide found. Alembic divergent heads have shipped at least three times (0.5.4, 0.6.2, 0.8.3); 0.10.0 and 0.10.2 flag slow migrations on large tables.
- **Silent loss, open:** #5273, #5079, #5251, #5218, #5077; also #3494 (empty content from an OpenRouter model silently losing facts). Several OpenClaw-plugin variants (#5081, #4594, #4537, #4813, #4828).
- **Resources:** #3355 (a single mental-model refresh on a 40K-node, 815K-link bank used 17.7 GB and was OOM-killed), #4492 (export on a 57K-unit bank OOM at 5 GB), #3891 (import 43 GB), #4130 (consolidation holds LLM slots for hours and starves retain). Your corpus is 32-47 records, so these are ceilings to know about, not near-term risks.
- **Stalls:** frequent but mostly fixed within days (#3194, #3594, #3881, #4696, #4763).
- **LLM compatibility:** several provider-specific failures (#5175, #4246, #4491, #5280, #3904, #5201). Model choice matters; test the extraction model you intend to use.
- **Benchmarks:** LongMemEval 83.6% with an open-source 20B model (91.4% with a larger backbone), LoCoMo 89.61%, from a December 2025 preprint co-authored by Vectorize and Virginia Tech. "Independent reproduction" is by a collaborator; the 91.4% is accuracy at an 8,192-token retrieval budget (a third-party review contrasts it with about 4,400 tokens for Zep, which I did not verify). I could not confirm what the RECON paper (arXiv 2607.16716) says about Hindsight. **No arm's-length reproduction found.**
- **Governance:** Vectorize AI, Inc.; MIT; about 3,470 commits, about 100 in the three days to 2026-10-06; top contributor has about 5.6x the next, so the bus factor is effectively one, though fixes for reported incidents have been fast.

---

## 8. Mismatch risks, bluntly

1. **The differentiating feature is the unreliable one.** Observations are where silent loss is reported, and they are LLM-derived text. Your lesson from cognee is that derived text needs a verbatim source and a way to check it; `include.chunks` and `source_fact_ids` provide the trail, but the consolidation drop (#5273) means the observation can silently lack facts.
2. **Using the part that fits discards the part that differentiates.** `chunks` mode is a good store and a reasonable cognee `CHUNKS` equivalent. It is not clearly better than what you have, apart from delete-by-document.
3. **Exact identifiers.** Your pointer records carry page ids. Expect to need `pg_search`, or ids duplicated in tags (tags *are* filterable) and in chunk text.
4. **No per-memory delete.** Workable with one document per record. It also means retraction in a bulk-retained session document removes the whole session.
5. **Plugin defaults are the wrong way round** for a deliberate-write design, and the built-in-memory mirror has no off switch.
6. **Version coupling.** Plugin requires Hermes >= 0.21.4 and client >= 0.10.1. Check which Hermes version your image pins before anything else; your Dockerfile installing `hindsight-client==0.6.1` suggests it still relies on the in-tree plugin that Nous removed on 2026-09-23.
7. **Operations.** A second stateful service, with the backup gap from the July disk failure still open. Restore wipes the target schema, so test it.

---

## 9. Recommended trial, if you run one

Throwaway instance, **latest 0.10.x**, slim image with OpenRouter embeddings, its own database, its own bank, not wired into production Hermes. Reuse `scripts/cognee/graph_vs_chunks.py` and the `shared` dataset (32-47 records) so the comparison is like for like.

- **Arm A: `chunks` mode, `LLM_PROVIDER=none`.** One `document_id` per record; ids in tags and chunk text. Measures: the 17 questions; exact-id lookup with a UUID, a Notion page id and a hyphenated title (try native, then `pg_search`); delete a record by document and confirm it is gone from recall; re-retain with the same `document_id`; `PATCH` invalidate.
- **Arm B: default extraction + consolidation**, with `retain_mission` and `observations_mission` set to keep pointers and dates verbatim. Measures: the same 17 questions; **count facts and observations against source documents to catch silent drops** (#5273 style); retain the same content twice; contradict a fact and see whether the observation records the change; read-back every write rather than trusting the acknowledgement.
- **Stop conditions:** any silent loss in arm B that read-back cannot detect; arm A not beating cognee `CHUNKS` on exact-id lookup; no working delete path; `observation_scopes` or the plugin append flow failing on a test of the shared bank.
- **Before the trial:** confirm the Hermes version in your image, and test whether Claude Desktop (or your existing claude.ai connector route) can reach a Bearer-authenticated remote MCP endpoint. The cognee MCP already works for you via a connector, so this is checkable, but I did not verify it.
- **Timing:** it fits next to the cognee graph-vs-CHUNKS verdict due around 2026-10-14.

---

## 10. Unconfirmed

1. Whether current Claude Desktop honours a native `url` + `headers` MCP entry for a self-hosted server.
2. Behaviour of exact UUID / hyphenated-id queries on the native lexical backend (code read only).
3. Whether chunks mode plus a configured LLM keeps consolidation working, and the quality of observations built from chunks.
4. Whether replace-mode re-retain of unchanged text skips the LLM.
5. Whether a dead server blocks the plugin's `initialize` / `is_available`; behaviour on Hermes older than 0.21.4.
6. Per-bank API keys; Cloud-only features beyond one hint.
7. Observation consolidation lag.
8. Whether the per-bank `mcp_enabled_tools` allowlist works as a clean read-only mode (code read, not run).
9. The exact LLM, judge and token budget behind 91.4%; what RECON says about Hindsight.
10. Targeted issue searches on embeddings, rerankers, auth, hallucination and per-retain cost (rate-limited).
11. Real resource use for your corpus size; documented RAM figures are minimums.

---

## 11. Sources

- Hindsight repository: https://github.com/vectorize-io/hindsight (README, `hindsight-docs/`, `hindsight-api-slim/`, `hindsight-integrations/hermes/`, `LICENSE`, releases, issues)
- Hermes integration docs: https://hindsight.vectorize.io/sdks/integrations/hermes
- Hermes plugin catalog: https://hermes-agent.nousresearch.com/docs/plugins/hindsight
- Hermes memory providers: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers
- Native memory provider announcement: https://hindsight.vectorize.io/blog/2026/04/06/hermes-native-memory-provider
- Changelog: https://hindsight.vectorize.io/changelog; FAQ: https://hindsight.vectorize.io/faq
- Hindsight is 20/20: https://arxiv.org/abs/2512.12818
- RECON: https://arxiv.org/abs/2607.16716 (not confirmed to cover Hindsight)
- Third-party review: https://github.com/akitaonrails/ai-memory/blob/main/docs/research-hindsight.md
- Vectorize press release: https://www.prnewswire.com/news-releases/vectorize-breaks-90-on-longmemeval-with-open-source-ai-agent-memory-system-302643146.html (vendor claim)
- Cloud Run cost report: https://github.com/KoshiirRa/midgley/issues/421
- Issues cited: #5273, #5079, #5251, #5218, #5077, #5267, #5305, #4845, #5171, #4945, #5262, #4662, #5158, #3509, #3355, #3494, #4130, #4642, #4691, #3216, #5175, #4246, #4491, #5280, #3904, #5201 (all in vectorize-io/hindsight)

# Honcho as a memory provider for Hermes + Claude Desktop

**Date:** 2026-09-26
**Subject:** plastic-labs/honcho — server **v3.2.1** (released 2026-09-22), MCP server **v3.0.1** (2026-09-22), Python/TS SDK **v2.5.1**
**Method:** cloned `github.com/plastic-labs/honcho` at commit `2eb27b6` (2026-09-25) and read the source, in-repo docs (`docs/v3/**`), `CHANGELOG.md`, `config.toml.example`, `.env.template`, `docker-compose.yml.example`, and the in-repo MCP server (`mcp/`). Claims below are marked where they come from docs vs. code. Anything I could not confirm is marked UNKNOWN.

---

## 1. Verdict

**Partly — but the "partly" is much better than it looks from the landing page, and the fit is good enough to be worth a trial.** Honcho's marketing is all about the dialectic (LLM-synthesised answers about a user), which is exactly the Cognee failure mode you are escaping. But underneath that, Honcho is a plain FastAPI + Postgres/pgvector message store with a **hybrid retrieval path (Postgres full-text + ILIKE substring + vector, fused with RRF) that returns the stored message rows verbatim**, arbitrary JSONB metadata on every message, a rich filter language over that metadata and `created_at`, and client-supplied `created_at` so you can backdate assertions. Every LLM-touching subsystem — deriver, summariser, dreamer, peer cards — can be switched off by config, and there is no automatic transcript capture at the server at all: nothing enters Honcho that your code did not POST.

The single biggest reason it is "partly" and not "yes": **individual messages cannot be deleted.** There is no `DELETE /messages/{id}` endpoint and the docs state it plainly. Your only retraction units are the whole session, the whole workspace, or a conclusion. For an append-only dated-assertion log that is survivable (you retract by writing a newer assertion, which is your stated conflict rule anyway), but it means a mistaken write with a wrong Notion page id is permanent unless you segregate assertions into small disposable sessions.

The runner-up reason: an **open, unfixed issue filed the day before this report** ([#1241](https://github.com/plastic-labs/honcho/issues/1241), 2026-09-25) reports the deriver writing its own prompt's few-shot example content into conclusions about a real user, with the real peer id substituted in — a confabulated-fact bug of exactly the Cognee class, and the reporter's attempts to suppress it via `custom_instructions` had zero effect. Your posture should therefore be: **deriver off, dialectic unused, `search` only.** If you are going to run Honcho that way, be honest that you are using it as a well-built Postgres hybrid-search service with an MCP front end — which it is, and which may still be the right call.

Second-order concern: the "assertion with a Notion page id" record should be modelled as a **Message**, not a **Conclusion**. Conclusions — the thing Honcho calls memory — have no metadata field, no client-settable timestamp, and their semantic query path requires an observer/observed peer pair and is vector-only with no lexical fallback. Messages have all the properties you need; conclusions do not.

---

## 2. Requirements table

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Custom model **and** embedding endpoints; OpenAI-compatible base URLs; self-hostable | **MEETS** | Per-module `MODEL_CONFIG__OVERRIDES__BASE_URL` + `__API_KEY_ENV` for deriver, summary, dream, and each dialectic level, and `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` for embeddings. `.env.template` names OpenRouter, Together, Fireworks, vLLM, Ollama, LiteLLM explicitly and gives `https://openrouter.ai/api/v1` as the worked example. Transports: `openai`, `anthropic`, `gemini`. Caveat in the same file: *"Models must support tool calling (function calling)."* Self-host = `docker compose up` with Postgres+pgvector+Redis. ([.env.template](https://github.com/plastic-labs/honcho/blob/main/.env.template), [config.toml.example](https://github.com/plastic-labs/honcho/blob/main/config.toml.example), [docker-compose.yml.example](https://github.com/plastic-labs/honcho/blob/main/docker-compose.yml.example)) |
| 2 | Usable from Claude Desktop via a credible MCP server | **MEETS** | First-party MCP server lives **in the main repo** at `mcp/`, versioned independently, **v3.0.1 released 2026-09-22**. Runs hosted (`mcp.honcho.dev`), as a Cloudflare Worker, over stdio, or as a Docker HTTP service wired into the same compose file. Point it at self-hosted with `HONCHO_API_URL`. ~35 tools incl. `search`, `get_session_messages`, `add_messages_to_session`, `list_conclusions`, `create_conclusions`, `delete_conclusion`. ([mcp/README.md](https://github.com/plastic-labs/honcho/blob/main/mcp/README.md), [mcp/CHANGELOG.md](https://github.com/plastic-labs/honcho/blob/main/mcp/CHANGELOG.md)) |
| 3 | Can be the memory for a personal life-management agent | **PARTLY** | It is architecturally fine as a store, and the repo **ships a Hermes Agent memory provider** (`hermes-plugin-honcho/`, a handoff copy of the provider that shipped inside `NousResearch/hermes-agent` under `plugins/memory/honcho/`) — so the Hermes integration already exists. But Honcho's opinionated layer is *peer modelling from dialogue*, not *life state*. Using it your way means running most of that layer switched off, i.e. using maybe 30% of the product. ([hermes-plugin-honcho/HANDOFF.md](https://github.com/plastic-labs/honcho/blob/main/hermes-plugin-honcho/HANDOFF.md)) |
| 4 | Retrieval returns stored text **verbatim** | **MEETS** | Two distinct paths. `search` returns raw `Message` rows — `{id, content, peer_id, session_id, metadata, created_at, workspace_id, token_count}` — no LLM in the path. `get_session_messages` / `list_conclusions` / `query_conclusions` likewise return stored rows. The LLM-synthesis path (`chat`/dialectic) is a *separate endpoint you choose to call*, and even it now takes `include_evidence=true` to return the exact conclusions and messages it read. Docs: *"Search returns the messages themselves. When you want a synthesized answer… use workspace chat."* ([search.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/search.mdx), [evidence.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/evidence.mdx)) |
| 5 | No uncontrollable automatic transcript capture | **MEETS** | The server ingests **nothing** on its own — messages arrive only via `POST .../messages`. All derived processing is switchable: `reasoning.enabled`, `summary.enabled`, `dream.enabled`, `peer_card.create` at workspace/session/**message** level; `observe_me: false` per peer; `DERIVER_ENABLED=false` and `EMBED_MESSAGES=false` server-wide. The MCP server's shipped instructions default to **recall-only** and tell the agent *"If the user hasn't asked for this conversation to be recorded, stay in recall mode."* The Hermes plugin does flush conversation turns on `on_session_end`, but has a hard gate: `saveMessages: false`. ([reasoning-configuration.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/reasoning-configuration.mdx), [mcp/instructions.md](https://github.com/plastic-labs/honcho/blob/main/mcp/instructions.md), `hermes-plugin-honcho/config_schema.py`) |
| 6 | Deliberate write of a single curated fact, retrievable shortly after | **MEETS** | `POST .../sessions/{id}/messages` with `content`, `metadata` (arbitrary JSONB), and an optional **client-supplied `created_at`**. Docs are explicit on latency: *"Keyword matches are available the instant a message is created. Semantic matches depend on the message's embedding, which is generated in the background, so a freshly created message may take a few seconds."* So a lexical/exact hit is immediate. `create_conclusions` is the other deliberate write path but is weaker (see §4). ([src/schemas/api.py `MessageCreate`](https://github.com/plastic-labs/honcho/blob/main/src/schemas/api.py), search.mdx) |
| 7 | Short queries + exact identifiers; lexical as well as semantic | **MEETS** | `src/utils/search.py` implements hybrid RRF. Critically, the lexical branch checks `re.search(r'[~\`!@#$%^&*()_+=\[\]{};\':"\\\|,.<>/?-]', query)` — a query containing special characters (a hyphenated UUID, a Notion page id, a dotted title) **bypasses tsquery entirely and does an escaped `ILIKE '%…%'` literal substring match**, ordered by recency. Non-special queries use `to_tsvector('english')` + `plainto_tsquery` ranked by `ts_rank`, **OR**'d with the same ILIKE as fallback. Set `EMBED_MESSAGES=false` and you have a purely lexical store. ([src/utils/search.py](https://github.com/plastic-labs/honcho/blob/main/src/utils/search.py)) |

Additional requirements you named in the brief:

| Topic | Verdict | Evidence |
|---|---|---|
| Arbitrary metadata + filter/query by it | **MEETS for messages, FAILS for conclusions** | `MessageCreate.metadata` is arbitrary JSONB; the filter language supports nested metadata, `and`/`or`/`not`, comparison operators, `in`, `contains`, and wildcards, and is exposed through MCP as `message_filters`. `ConclusionCreate` has only `content`, `observer_id`, `observed_id`, `session_id` — **no metadata field at all**. ([using-filters.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/using-filters.mdx), `src/schemas/api.py:681`) |
| Append-only dated assertions; "what was true as of date X" | **PARTLY** | Messages are effectively append-only (no delete, no content update — `MessageUpdate` changes metadata only) and `created_at` is client-settable, so backdating works. Filters support `{"created_at": {"gte": …, "lte": …}}`. But there is **no as-of/bitemporal query primitive** — no `valid_at`/`invalid_at` the way Graphiti has. "Most recent wins" is something your agent implements by sorting, not something Honcho enforces. |
| Delete a single record | **FAILS for messages, MEETS for conclusions** | Docs: *"Peers and individual messages cannot be deleted."* There is no `DELETE` route in `src/routers/messages.py`. Deletable units: session (`202`, async cascade, no completion signal), workspace (`202`), conclusion (`204`, immediate). Deletion is permanent, no soft-delete. Gotcha in the docs: derived conclusions are stored at workspace level and **survive session deletion**. ([deleting-data.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/deleting-data.mdx)) |
| Self-hosted vs cloud parity | **PARTLY — not code-gated, but not equivalent** | No licence check, feature flag or entitlement gate exists in the server source; the dreamer, dialectic, scopes, webhooks and MCP server are all in the AGPL-3.0 repo. **But** the managed cloud runs Plastic Labs' own proprietary reasoning model ("Neuromancer XR", an 8B fine-tune of Qwen3-8B for conclusion extraction) which is not in the repo — self-hosters bring a generic LLM. There is also no first-party cloud↔self-host migration tooling ([#721](https://github.com/plastic-labs/honcho/issues/721) is an open request for it). Immaterial if you disable the deriver anyway. |

---

## 3. What Honcho actually is, in plain terms

**The data model is four things:**

- **Workspace** — a tenant/namespace. Auth keys are issued at this level. Config cascades from here.
- **Peer** — any entity: a human, an agent, an NPC. Everything in Honcho is oriented around building a *representation* of a peer. Peers are many-to-many with sessions.
- **Session** — a thread with temporal boundaries, containing messages, with one or more peers in it. The docs explicitly suggest using a single-peer session as an import channel for non-conversational data (emails, documents, files). Sessions can be grouped into named **scopes** that bound recall.
- **Message** — the atomic stored row: `content`, authoring `peer_id`, `session_id`, arbitrary JSONB `metadata`, `created_at`, `token_count`. This is the only thing that is stored verbatim by you and returned verbatim to you.

**On top of that sit four derived layers, all optional:**

- **Deriver** (async, per message) — an LLM extracts *conclusions* about a peer: `explicit` (directly stated) and `deductive`.
- **Summariser** (async) — rolls up short/long session summaries every N messages.
- **Dreamer** (periodic, default every ≥8h when idle) — revisits conclusions, consolidates them, draws `inductive` and `contradiction` conclusions, and updates **peer cards** (a short list of stable biographical facts).
- **Dialectic / `chat`** — an LLM *agent* with search tools that runs inline on your request, searches conclusions and messages, and writes a synthesised prose answer. Five `reasoning_level`s (`minimal`→`max`) controlling model, tool budget and iteration count.

**A Conclusion** is a row with `id`, `content`, `observer`, `observed`, optional `session_name`, `level` (explicit/deductive/inductive/contradiction), `source_ids` (premise chain), `times_derived`, `created_at`. No metadata. `created_at` is server-assigned.

**Working vs long-term:** the deriver maintains a bounded "working representation" (`WORKING_REPRESENTATION_MAX_OBSERVATIONS = 100`) which is the recent, hot slice; the durable store is the conclusion rows in the vector collection plus the peer card. `get_context()` assembles a token-budgeted blend of summaries + recent messages (+ representation and card if you name a target peer) for prompt injection.

**Retrieval paths, ranked by how much LLM sits between you and the bytes:**

| Path | LLM in the loop? | Returns |
|---|---|---|
| `get_session_messages`, `get_session_message` | none | raw message rows |
| `search` (workspace / peer / session) | embedding model only for the query vector | raw message rows, RRF-fused from FTS + ILIKE + vector |
| `list_conclusions` (filterable, e.g. `level: explicit`) | none | raw conclusion rows |
| `query_conclusions` | embedding model only | raw conclusion rows; **requires** observer + observed |
| `get_context` | none at query time (uses pre-generated summaries) | assembled messages + summaries |
| `chat` / `workspace_chat` | **full agent, synthesises prose** | prose, optionally + `evidence` (the rows it read) |

**Infrastructure:** two processes — FastAPI API server and a `src.deriver` worker — plus Postgres with pgvector and Redis. Vector store is pluggable: `pgvector` (default), `turbopuffer`, `lancedb`, `qdrant`. Licence **AGPL-3.0**.

---

## 4. Mismatch risks, bluntly

**1. Messages cannot be deleted. Ever.** This is the hard one. Your model is an append-only log of assertions, which sounds compatible, but "append-only" in your design still assumes you can excise a record written in error — a wrong Notion page id, a fact about the wrong person. Honcho's only escape hatches are deleting the enclosing session (async `202`, and there is no endpoint that tells you when the cascade finished) or nuking the workspace. **Mitigation:** partition assertions into many small sessions (e.g. one per month, or one per topic), so the blast radius of a session delete is bounded. Or accept metadata-based tombstones (`MessageUpdate` can change metadata) and filter `{"metadata": {"retracted": {"ne": true}}}` on every read — which puts the correctness burden on every caller forever.

**2. Conclusions — the thing Honcho calls "memory" — are the wrong container for your records.** No metadata field means no structured Notion page id, no `kind: assertion`, no `subject`. No client `created_at` means no backdating. `query_conclusions` is vector-only with no lexical fallback and *requires* an observer/observed pair, so it cannot do the exact-identifier lookup that is the whole point of the pointer pattern. You will be storing your assertions as Messages in a dedicated session and treating conclusions as something you either ignore or leave the deriver to generate as a bonus. That is a supported use of the system but it is against the grain of the product, and it means every Honcho blog post, benchmark and default is optimising for a path you are not on.

**3. The dialectic is where all the engineering attention goes, and it is the Cognee failure mode.** `chat()` is an LLM agent writing prose. It will paraphrase. It will truncate names and get dates wrong exactly as Cognee did — that is not a Cognee bug, that is what "synthesise an answer from retrieved context" does. Honcho's `include_evidence` mitigation (added 2026-09-15 in v3.2.0) is genuinely good and the right instinct, but note the docs' own caveat: evidence is *"collated from what the agent accessed"* and *"over-reports — a conclusion appears because it was seen, not because the answer used it."* **Your discipline has to be: never route identifier lookups through `chat`. Use `search`.** Both are on the same MCP connection and Claude Desktop will happily pick the wrong one. The Hermes plugin's `recallMode` default is `hybrid`, which *auto-injects* dialectic output into the system prompt — you would want `tools`.

**4. The Hermes plugin's default is exactly the thing you are running away from.** `saveMessages` defaults to `true` and `on_session_end` flushes every conversation turn into a Honcho session. `recallMode` defaults to `hybrid` with dialectic injection. Those are both one config line away from off, but the shipped defaults are "capture everything, inject synthesised prose" — the Cognee shape. Also: the plugin is an **orphaned handoff**. `HANDOFF.md` says Nous Research published it so Plastic Labs could take it over, and that it is *"not an officially maintained Nous plugin."* I could not confirm that Plastic Labs has adopted it. It pins `honcho-ai>=2.2.0,<3` (current SDK is 2.5.1, so fine today) and imports private Hermes CLI internals (`_curses_select`, `_prompt`) that the handoff notes themselves flag as liable to break.

**5. Cost and background LLM burn.** If you leave the deriver, summariser and dreamer on, every message you write triggers LLM calls, and the dreamer wakes on a schedule to re-reason over your whole corpus. For a personal store this is probably fine money-wise but it is nonzero and it is exactly the class of background behaviour that produced Cognee's junk graph. Open issues bear this out: the deriver leaks prompt examples into real conclusions ([#1241](https://github.com/plastic-labs/honcho/issues/1241)), extracts ephemeral facts that pollute long-term memory ([#1027](https://github.com/plastic-labs/honcho/issues/1027)), and non-streaming dialectic keeps billing after the client disconnects ([#1050](https://github.com/plastic-labs/honcho/issues/1050)). Recommended posture: `DERIVER_ENABLED=false`, `summary.enabled=false`, `dream.enabled=false`, keep `EMBED_MESSAGES=true` for semantic recall. That reduces Honcho to "Postgres + pgvector + hybrid search + a good filter language + an MCP server" — which is genuinely what you want, but see §5: at that point a much smaller thing would also do.

**6. Two agents, one store — mostly fine, one wrinkle.** Both Hermes and Claude Desktop can point at the same self-hosted instance and the same workspace. But session identity differs: the Hermes plugin resolves sessions by strategy (`per-directory` default; set `global` or an explicit override for a shared assertion log), while MCP `create_session` takes a literal id. You must pin both to the same session id or your assertions land in two silos that only workspace-level search reunites. Also `add_messages_to_session` over MCP accepts `metadata` but **not** `created_at`, so Claude Desktop cannot backdate — only the REST/Hermes path can.

**7. Things I could not verify.** Whether self-hosted is feature-equivalent to cloud (no gating found, but no explicit statement either). Real-world retrieval quality on a personal-scale corpus — the published evals are the vendor's own. Whether `plainto_tsquery('english', …)` stemming hurts on very short queries (one or two tokens) in practice; the ILIKE OR-branch should cover it but I did not measure.

---

## 5. Maturity and known weaknesses

**Numbers (live, 2026-09-26):** 7,353 stars · 907 forks · **83 open issues** · **67 open PRs** · 61 contributors · AGPL-3.0. Cadence is roughly weekly since late August 2026 (v3.1.0 2026-08-25 → v3.2.1 2026-09-22), after a ~6-week quiet spell in July. Active, well-documented, clearly staffed. Version history lives in `CHANGELOG.md` and git tags — only one GitHub *Release* object exists, so don't judge cadence from the Releases tab.

**Breaking changes to know about:**

- **v3.0.0 (2026-01-19)** renamed "Observations" to "Conclusions" across API and SDKs, plus "API route renaming for consistency". Not labelled breaking; functionally is. No `MIGRATION.md` in the repo.
- **v3.1.0 (2026-08-25)** — explicitly labelled *"Breaking config change"*: `DERIVER_REPRESENTATION_BATCH_MAX_TOKENS` split into two new env vars; deployments on the old name must migrate.
- **v3.2.1 (2026-09-22)** — `source_ids` changed from `null` to `[]` on explicit conclusions. Silent client breakage if you checked for null.
- Python floor raised to **≥3.13** (May 2026). Self-hosters on 3.10–3.12 must upgrade.

**The four open issues that matter most for your use case — all verified directly:**

| Issue | Opened | Why it matters here |
|---|---|---|
| [#1241](https://github.com/plastic-labs/honcho/issues/1241) — *"Deriver prompt example content leaks into conclusions about real peers"* (also [#1230](https://github.com/plastic-labs/honcho/issues/1230), follow-up to #1028/#985) | 2026-09-25, open | On managed 3.2.0 the deriver writes **its own prompt's few-shot example content** into conclusions about a real user, substituting the real peer id for the example's "alice". The reporter tried `reasoning.custom_instructions` at workspace, session and message level — *"0 of 8 conclusions applied it at each level."* This is a confabulated-fact bug of exactly the Cognee class. It is the single strongest argument for running with `DERIVER_ENABLED=false`. |
| [#1082](https://github.com/plastic-labs/honcho/issues/1082) — *"conclusions query ranks recent/generic conclusions over query-relevant ones"* | 2026-08-26, open | Conclusion semantic search returns recency over relevance even when on-topic conclusions exist. **Surfaced from a hermes-agent integration** ([NousResearch/hermes-agent#95266](https://github.com/NousResearch/hermes-agent/issues/95266)), traced by a Hermes maintainer to the Honcho service rather than the client. Another reason to store your assertions as messages, not conclusions. |
| [#1048](https://github.com/plastic-labs/honcho/issues/1048) — *"Filtered pgvector HNSW conclusion queries silently under-return"* | 2026-08-23, open | With default `hnsw.ef_search=40` and `hnsw.iterative_scan=off`, the approximate scan is filtered *after* the fact, so out-of-scope candidates eat the budget and a filtered query can return **zero results when matches exist**. Documented against `src/crud/document.py` (conclusions), but message search uses the same pgvector pattern, so treat filtered semantic message search as suspect too. The lexical branch is unaffected. |
| [#1244](https://github.com/plastic-labs/honcho/issues/1244) — *"search_messages results are head-clamped to MAX_TOOL_OUTPUT_CHARS, silently discarding later snippets"* | 2026-09-25, open | The formatted result is truncated from the head with no signal to model or telemetry, so a re-search returns the same size payload regardless of query. **Scope check (I read the code):** this is `_format_message_snippets()` in `src/utils/agent_tools.py` — the **dialectic agent's internal tool**, not the public `/search` REST endpoint, which goes through `src/utils/search.py` and returns paginated ORM rows. So it degrades `chat`, not `search`. It is nonetheless a clean illustration of why the synthesis path is the untrustworthy one. |

**Recurring pattern worth naming:** the queue/worker layer has silently marked failed work as completed at least three times ([#1236](https://github.com/plastic-labs/honcho/issues/1236) open, [#963](https://github.com/plastic-labs/honcho/issues/963) and [#728](https://github.com/plastic-labs/honcho/issues/728) closed). Same failure class, three different manifestations — a design-level weakness in failure accounting rather than three unrelated bugs.

**Self-hosting friction:** [#1238](https://github.com/plastic-labs/honcho/issues/1238) (Redis RDB save fails under s6-overlay in the "honcho-aio" image, open); native Windows unsupported ([#1075](https://github.com/plastic-labs/honcho/issues/1075), closed `not_planned`); a cluster of local-model/llama.cpp friction reports ([#1174](https://github.com/plastic-labs/honcho/issues/1174), [#1175](https://github.com/plastic-labs/honcho/issues/1175), [#1180](https://github.com/plastic-labs/honcho/issues/1180)) closed as "completed" but tagged *documentation* — i.e. closed with a doc clarification, not a code fix for the hardcoded timeout / hardcoded model name behaviour reported. Treat those three as unresolved in practice. Relevant to you: **your plan routes at least the embedding model, and possibly more, at a local or proxied endpoint.**

**Community reception: there effectively isn't one.** Two Show HN submissions (Jan 2026, Apr 2026), **zero comments on each**. A 2024 Plastic Labs dialectic post submitted by a cofounder's own account, zero comments. Targeted searches across r/LocalLLaMA, r/AI_Agents, r/mcp, r/MachineLearning returned **no threads at all**. One independent-looking review exists ([dev.to/andrew-ooo](https://dev.to/andrew-ooo/honcho-review-plastic-labs-agent-memory-layer-2026-2kb4)) calling it "the cleanest open-source memory layer I've seen in 2026" while flagging "more operational complexity than lightweight options" — but its performance claims cite Plastic Labs' own published benchmarks rather than re-measuring, so read it as informed commentary, not a production report. **Conclusion: there is not enough independent discourse to characterise reception either way.** Every concrete complaint on record comes from GitHub issues filed by people actually running it. That is not damning — it is a young project with real users and a good issue template — but it does mean you would be an early adopter with no peer reports to fall back on.

**MCP packaging gotcha:** the npm package `@honcho-ai/mcp` exists but is stale — latest **2.2.0, published 2026-01-13**, and registry-listed as Apache-2.0 (the in-repo code is under the repo's AGPL-3.0; I could not resolve which licence governs that npm artifact). The maintained server is the in-repo `mcp/` at v3.0.1, distributed by git clone + bun/Docker/hosted Worker, **not** npm. Do not `npm install @honcho-ai/mcp`.

---

## 6. Alternatives

**Plain Postgres + pgvector + `tsvector`, behind a ~300-line MCP server.** Given that you intend to disable the deriver, summariser and dreamer, this is *literally what Honcho reduces to* — Honcho's own `src/utils/search.py` is FTS + ILIKE + pgvector fused with RRF, which is about 150 lines you could write. You would get: real `DELETE` on a single row, a schema you control (dated assertion, subject, `notion_page_id` as a first-class indexed column rather than a JSONB key), bitemporal `valid_from`/`valid_to` if you want as-of queries, and zero background LLM spend. You lose: nothing you are planning to use, except someone else maintaining it. Against it: you have already built and abandoned a bespoke memory layer once (Mnemosyne/Cognee), so "write your own" has a track record here. But the gap between "plain Postgres" and "Honcho with everything off" is now small enough that the honest comparison is maintenance burden, not capability.

**Graphiti (getzep/graphiti), MIT.** Temporal knowledge graph with **explicit bitemporal fact validity (`valid_at` / `invalid_at`)** — the only option here that natively answers "what was true as of date X" and natively implements "most recent wins" as a system property rather than an agent convention. Retrieval is embeddings + BM25 + graph traversal. Better than Honcho on your temporal requirement and on lexical search. Worse on: it requires Neo4j/FalkorDB/Kuzu to operate, and you have just measured that a knowledge graph bought you nothing (2,273 predicates, 1,609 used once, 78% singleton entities) — Graphiti would likely reproduce that, since the graph is LLM-extracted the same way. Adopt only if the temporal-validity model, not the graph, is what you want. ([getzep.com/product/open-source](https://www.getzep.com/product/open-source/))

**An off-the-shelf simple MCP memory server (e.g. basic-memory / the reference `memory` server family).** Markdown- or SQLite-backed, deliberate writes only, no background LLM, trivially inspectable and trivially deletable, file-diffable in git. Fits "preferences too small to justify a Notion page" perfectly and fits the pointer pattern fine. Worse than Honcho on: multi-agent concurrency from two processes, and semantic recall quality at any scale — most are lexical-only or naive-embedding. Also worth naming honestly: you already run Mnemosyne with its own MCP surface and a pluggable provider; pointing that provider at plain Postgres may be cheaper than migrating to a fourth system.

---

## 7. Recommended posture if you trial Honcho

Not requested, but it falls out of the above and is short:

- Self-host via `docker compose`; Postgres+pgvector+Redis+api+deriver+mcp are all in the shipped compose file.
- `DERIVER_ENABLED=false`, workspace config `{"summary":{"enabled":false},"dream":{"enabled":false},"peer_card":{"create":false},"reasoning":{"enabled":false}}`. Keep `EMBED_MESSAGES=true`.
- Embeddings: `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` at a local or OpenAI-compatible endpoint. (If you also want the dialectic later, note the tool-calling requirement on whichever model you route there.)
- One workspace, one peer for you, **one dedicated session** for curated assertions (or one per month, to bound the delete blast radius), pinned identically in the Hermes plugin (`sessions` override or `sessionStrategy: global`) and in MCP.
- Write assertions as **Messages** with `metadata` carrying `{kind: "assertion", notion_page_id: "…", subject: "…"}` and an explicit `created_at` where backdating matters.
- Retrieve with `search` + `message_filters`. Never with `chat`. Set the Hermes plugin to `recallMode: tools`, `saveMessages: false`.
- Retract by writing a newer assertion (your stated rule); reserve session deletion for genuine errors.

---

## 8. Sources

All source links resolved against `github.com/plastic-labs/honcho` at commit `2eb27b6c` (2026-09-25), server v3.2.1.

- Repo: <https://github.com/plastic-labs/honcho> — AGPL-3.0
- [README.md](https://github.com/plastic-labs/honcho/blob/main/README.md) — self-hosting, configuration
- [CHANGELOG.md](https://github.com/plastic-labs/honcho/blob/main/CHANGELOG.md) — v3.0.0 2026-01-19; v3.2.0 2026-09-15; v3.2.1 2026-09-22
- [docs/v3 Architecture](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/core-concepts/architecture.mdx)
- [docs/v3 Search](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/search.mdx)
- [docs/v3 Deleting Data](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/deleting-data.mdx)
- [docs/v3 Reasoning Configuration](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/reasoning-configuration.mdx)
- [docs/v3 Using Filters](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/using-filters.mdx)
- [docs/v3 Evidence](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/evidence.mdx)
- [docs/v3 Chat Endpoint](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/chat.mdx)
- [src/utils/search.py](https://github.com/plastic-labs/honcho/blob/main/src/utils/search.py) — hybrid RRF, FTS/ILIKE branch
- [src/schemas/api.py](https://github.com/plastic-labs/honcho/blob/main/src/schemas/api.py) — `MessageCreate`, `Conclusion`, `ConclusionCreate`
- [src/routers/conclusions.py](https://github.com/plastic-labs/honcho/blob/main/src/routers/conclusions.py)
- [.env.template](https://github.com/plastic-labs/honcho/blob/main/.env.template), [config.toml.example](https://github.com/plastic-labs/honcho/blob/main/config.toml.example)
- [mcp/README.md](https://github.com/plastic-labs/honcho/blob/main/mcp/README.md), [mcp/instructions.md](https://github.com/plastic-labs/honcho/blob/main/mcp/instructions.md), [mcp/CHANGELOG.md](https://github.com/plastic-labs/honcho/blob/main/mcp/CHANGELOG.md)
- [hermes-plugin-honcho/HANDOFF.md](https://github.com/plastic-labs/honcho/blob/main/hermes-plugin-honcho/HANDOFF.md)
- Issues verified directly via the GitHub API on 2026-09-26: [#1241](https://github.com/plastic-labs/honcho/issues/1241), [#1244](https://github.com/plastic-labs/honcho/issues/1244), [#1082](https://github.com/plastic-labs/honcho/issues/1082), [#1048](https://github.com/plastic-labs/honcho/issues/1048); referenced from the maturity sweep: [#1230](https://github.com/plastic-labs/honcho/issues/1230), [#1236](https://github.com/plastic-labs/honcho/issues/1236), [#1238](https://github.com/plastic-labs/honcho/issues/1238), [#1027](https://github.com/plastic-labs/honcho/issues/1027), [#1050](https://github.com/plastic-labs/honcho/issues/1050), [#721](https://github.com/plastic-labs/honcho/issues/721)
- Independent review (single, non-replicated): <https://dev.to/andrew-ooo/honcho-review-plastic-labs-agent-memory-layer-2026-2kb4>
- Graphiti / Zep open source: <https://www.getzep.com/product/open-source/>
- PyPI `honcho-ai` 2.5.1: <https://pypi.org/project/honcho-ai/>

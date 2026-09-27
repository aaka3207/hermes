# OpenViking as a memory provider for Hermes + Claude Desktop

Research date: 2026-09-26. Primary sources: the repo at commit `a09a9d2` (2026-09-26), release `v0.4.21` (tag 2026-09-18, published 2026-09-20), the in-repo docs under `docs/en/`, source code read directly, and the live GitHub issue tracker.

> **Verdict changed during research.** An initial pass over the repo and docs supported a cautious "partly fits, worth a trial." A subsequent sweep of the live issue tracker turned up open bugs that invert that — most importantly, off-switches that are documented but not wired, and a broken backup path. The verdict below is the corrected one.

---

## 1. WHAT IT IS — disambiguation

**Found, and it is the right kind of thing.** The project evaluated here is:

- Repo: **https://github.com/volcengine/OpenViking**
- Site: **https://openviking.ai/**
- Tagline (repo description): *"Self-evolving Context Database for AI Agents. Unify Agent Memory, Knowledge RAG and Skills."*
- Owner: Volcano Engine (Volcengine), ByteDance's cloud arm.
- Licence: **AGPL-3.0** for the main project; `examples/` and `crates/ov_cli` are Apache-2.0 (`docs/en/faq/faq.md:404`).

Confidence it is the right project: **high**. It is explicitly a memory layer for AI agents — it ships a memory namespace (`viking://~/memories`) with built-in `preferences`, `events`, `entities`, `profile` types (`docs/en/api/16-memory.md`), a `remember`/`forget` tool pair, a session-commit memory-extraction pipeline, and a built-in MCP server. There is no ambiguity about the category.

### Other things called "OpenViking" — none of them is what you want

- `LoicHmh/openviking` — a personal fork/mirror of the same Volcengine project, not an independent thing.
- `purisev/openviking-memory`, `dieguscl/openviking-memory-docs` — third-party community wrappers/doc sets *about* the Volcengine project.
- Assorted blog/review/YouTube content (Red Hat Developer, Wavect, FAUN) — commentary on the same project.

So: one real project, several derivative repos. I evaluated the upstream Volcengine one.

### ⚠️ A name collision you must not trip over

`docs/en/agent-integrations/05-hermes.md` documents a first-class OpenViking memory provider for **"Hermes Agent" by Nous Research** (`hermes-agent.nousresearch.com`). **That is not your Hermes.** Your Hermes is your own self-hosted Python agent. The `hermes memory setup openviking` command in those docs belongs to Nous Research's product and will not exist on your system. Do not read that page as "OpenViking already supports my agent." What it *does* tell you is that OpenViking's HTTP API is considered a reasonable integration target by at least one other agent project, which is mildly encouraging for writing your own provider.

---

## 2. Verdict

**The architecture fits. The implementation, today, does not. Do not adopt it yet — re-evaluate at 0.5.x or 1.0.**

The design is genuinely right for you, and better than Cognee's. **OpenViking separates the LLM from the read path**: the digest (`rewrite`) **defaults to `false`** and lands in a *separate* field beside the un-paraphrased `rendered` block, so the summariser never destroys verbatim text (`docs/en/api/06-retrieval.md:653,760-762`). It exposes `read`, `grep` (real regex), `glob`, `write` and `forget` as first-class MCP tools — the exact "deliberate write, verbatim read, exact-identifier lookup" triad your Notion-pointer design needs. Raw transcripts are never vectorised. If you were judging the design document, this would be a yes.

**But three things found in the live issue tracker say wait.** In order of how much they should matter to you:

1. **The Cognee failure you are fleeing already exists here, filed two days ago.** Issue [#5382](https://github.com/volcengine/OpenViking/issues/5382) (open, 2026-09-24): subagent prompts occupy the `user` role in transcripts, so the extractor stores **agent instructions as the human's durable preferences** — "output in Chinese, stay under 1300 words" became a `preferences/user/` memory and was auto-recalled as *"the user explicitly asked…"*. That is, precisely, an agent recalling its own output and reporting it as a stored fact about you.

2. **Off-switches are documented but not wired.** The same issue reports `skipSubagentSessions` is declared in the plugin schema and **nothing reads it**. Issue [#5402](https://github.com/volcengine/OpenViking/issues/5402) (open, 2026-09-26) reports the same for `recallExcludeUris` — schema-documented, never passed to search, in both the Claude Code (v0.6.4) and Codex (v0.10.5) plugins, though the `dsh` variant did get the fix. **I verified both directly.** This matters more than either individual bug: my requirements table rests partly on configuration switches, and this project has a demonstrated pattern of shipping knobs that do nothing. A documented flag here is not evidence the behaviour is controllable. Given that you are "tired of being told something will work and finding out it does not," this is the finding to weigh heaviest.

3. **Silent data loss is the house failure mode, and the backup you would rely on is currently broken.** `ov snapshot commit` has failed on every path since v0.4.19 — one user's automated nightly backup had been **silently failing since upgrading** ([#5403](https://github.com/volcengine/OpenViking/issues/5403), open 2026-09-26). v0.4.15 was a hotfix for an unpinned `xxhash` that made **newly generated vectors silently fail to persist while tasks reported success**. Add [#5406](https://github.com/volcengine/OpenViking/issues/5406) (unrecoverable HTTP 500 on ACL reads with no API recovery path — fix requires stopping the container and hand-editing RocksDB), [#3508](https://github.com/volcengine/OpenViking/issues/3508) (extraction silently drops all but the first memory when the LLM returns a list; hits llama.cpp), and 24h of dead memory capture with no alert. My earlier recommendation to "take a snapshot and practise a restore" as your safety net **does not currently work**.

Standing caveats: automatic extraction may delete and merge existing memory files with no apparent exemption for hand-written records; **hybrid search is off by default** (`sparse_weight: 0.0`) with no BM25 fallback, so exact-identifier lookup must go through `grep`, not the semantic verb; and the project is ~9 months old, still 0.4.x, shipping roughly **one release every 5 days** with breaking changes in most of them.

**What would change my mind:** the declared-but-unwired config class of bug being cleared, `ov snapshot` working, and a release where the "Compatibility and Migration" section is empty. None of that is far-fetched — the team ships fast and the engineering is serious. It is just not true today.

---

## 3. Requirements table

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Custom model **and** embedding endpoints, arbitrary OpenAI-compatible base URLs, genuinely self-hostable | **MEETS** | `embedding.dense` and `vlm` each take `provider` + `api_base` + `api_key` + `model`. Providers include `openai`, `azure`, `ollama`, `litellm`, `local`, `jina`, `voyage`, `cohere`, `gemini` (`docs/zh/guides/01-configuration.md:306`). Shipped example config has an Ollama block at `http://localhost:11434/v1` with `nomic-embed-text` (`examples/ov.conf.example:138-147`). OpenRouter is explicitly documented incl. `extra_headers`/`extra_body` provider routing (`docs/zh/guides/01-configuration.md:382-403`). Self-host = one container + `docker-compose.yml` in repo root; AGPL-3.0, no activation. |
| 2 | Usable from Claude Desktop (official/credible MCP server) | **MEETS** | MCP endpoint is built into the server process at `http://<server>:1933/mcp` — no separate process. 16 tools. Claude.ai / Claude Desktop listed as a verified platform via **native OAuth 2.1** (DCR + PKCE + opaque tokens, SQLite-backed, Studio consent screen) — no third-party proxy needed (`docs/en/guides/06-mcp-integration.md`, `docs/en/guides/11-oauth.md`). |
| 3 | Capable of being memory for a personal life-management agent | **PARTLY** | Built-in memory types map well: `preferences/`, `events/`, `entities/`, `profile.md` (`docs/en/api/16-memory.md`). But the project's centre of gravity is visibly **coding agents** — the `purpose` presets are `chat` and `coding`, most integrations and plugins are coding harnesses, and the benchmark story is LoCoMo + tau2-bench. `chat` purpose exists and weights `events:3, entities:3`. No evidence of anyone running a life-management assistant on it. |
| 4 | Retrieval returns stored text **verbatim**, not LLM-paraphrased | **MEETS** | `rewrite` defaults to **`false`**; when used it produces a separate `digest` field and the unrewritten `rendered` is still returned (`docs/en/api/06-retrieval.md:653`, `:760-762`). `read(uris)` returns exact file content. `find(read_content=true)` inlines each hit's real content. `grep` returns matching lines with `before_context`/`after_context`. The LLM in `search()` is on the **query** side (intent analysis / query rewriting), not the answer side (`docs/en/concepts/07-retrieval.md`). ⚠️ Caveat below on default tiers. |
| 5 | No uncontrollable automatic transcript capture | **PARTLY** (downgraded from MEETS on live-issue evidence) | *Architecturally yes:* extraction fires only on explicit `session.commit()`; raw MCP does not auto-capture; **raw transcripts are never vectorised** (`docs/en/api/14-ovpack.md:160`), so `find`/`search` can never return `messages.jsonl`. Server-side `memory_policy` (`self.enabled:false`+`peer.enabled:false`) genuinely gates memory writes in code (`session/session.py:216-227`). *In practice, not reliably:* **two documented off-switches are declared and never read** — `skipSubagentSessions` ([#5382](https://github.com/volcengine/OpenViking/issues/5382)) and `recallExcludeUris` ([#5402](https://github.com/volcengine/OpenViking/issues/5402)), both open, both verified by me. And #5382 shows capture actively misattributing **agent instructions as the user's own preferences** — your exact Cognee complaint. Verify every switch empirically; do not trust the schema. |
| 6 | Deliberate write API — single curated fact, retrievable shortly after | **MEETS** | MCP `write(uri, content, mode, wait, timeout)`; `viking://~/memories/...` is writable (`mcp_endpoint.py:1053-1090`). File bytes land **before the call returns**; `wait=true` additionally blocks until semantic/vector refresh completes, so a follow-up search sees it. `edit(uri, old_string, new_string)` for targeted changes. No LLM extraction on this path (though an abstract sidecar is generated alongside, leaving your bytes untouched). |
| 7 | Retrieval on short queries and exact identifiers (UUID, page title) | **MEETS via `grep`, FAILS via semantic search** | `grep` is true regex exact matching, and is the right tool for a UUID (`docs/en/api/06-retrieval.md:800-817`). `glob` for filenames; `read` for a known URI; `tags` for `k=v` AND-filters. **But do not expect the semantic verb to do this**: `sparse_weight` defaults to **`0.0`** — hybrid is off — and the docs are explicit that there is *"**no automatic BM25 or other sparse-vector fallback**"* (`docs/en/guides/01-configuration.md:652-654`). Sparse also requires a Doubao embedding model, so it is effectively unavailable on a local/OpenAI setup. Two further caveats: above 10,000 L2 files `grep` switches to BM25 recall + local regex and **can drop candidates** — force `"grep": {"engine": "fs"}` to keep it exhaustive; and the Claude Code plugin skips recall entirely below `OPENVIKING_MIN_QUERY_LENGTH=3`. |

---

## 4. What it actually is, in plain terms

A server (Python + Rust, one process, default port 1933) that presents everything an agent knows as a **virtual filesystem under `viking://` URIs**, and lets agents use filesystem verbs on it.

- **Content** lives in AGFS/RAGFS — real files, backend `localfs`, `s3fs`, or `memory`.
- **A vector index sits beside it** holding only URIs, vectors, and metadata — *not* file content. "All content read from AGFS; vector index only stores references" (`docs/en/concepts/05-storage.md`). This is the important design choice: the index is a pointer table, so the authoritative bytes are always the file.
- **Three tiers per node**: L0 `.abstract.md`, L1 `.overview.md`, L2 the actual content. Agents can scan summaries before paying to read bodies. The L0/L1 sidecars are LLM-generated *at ingest time*.
- **Three namespaces**: `viking://~/memories`, `viking://~/resources`, `viking://~/skills`.
- **Access paths**: HTTP REST API, Python/Go/TypeScript SDKs, an `ov` CLI, a built-in MCP endpoint, and a web UI at `/studio`.
- **Storage requirements are refreshingly light**: no Postgres, no pgvector, no external graph DB, nothing to run alongside it. Local filesystem + a local vector backend is the default. Remote options are `http`, `volcengine`/`vikingdb`, and an opt-in NVIDIA cuVS GPU backend. Note the flip side: **pgvector is not supported** — there is an open feature request for it — so you take the bundled vector store as-is. Git-backed snapshots give commit/history/restore over the whole tree.

**On the graph scepticism you asked for:** OpenViking does not sell itself as a knowledge graph, and I found no graph database in the storage architecture. Its structural claim is a *hierarchy* (a directory tree with summaries), not a predicate graph. The retrieval algorithm is a priority-queue walk down that tree with score propagation (`docs/en/concepts/07-retrieval.md`), where `score_propagation_alpha` defaults to `1.0` — meaning **the parent's score is ignored entirely and only the child's own embedding score counts**. In other words, by default the hierarchy contributes navigation and scoping, not ranking. That is an honest design, and it sidesteps the failure mode you measured in Cognee (2,273 predicates, 1,609 singletons) because there is no predicate vocabulary to degenerate. But be clear-eyed: the retrieval work is being done by embeddings + your explicit scoping, same as before. The win is that scoping is explicit and inspectable rather than emergent.

### How your design maps onto it

Your intended division of labour maps unusually cleanly:

- Dated assertions → `write("viking://~/memories/events/2026-09-26-notion-page-created.md", ...)`. No extraction, no LLM, exactly your bytes.
- Notion pointer records → the page id is in the file body; recover it with `grep` on the UUID, or `read` on the URI.
- Small preferences → `viking://~/memories/preferences/`.
- "Most recent wins" → `created_at`/`updated_at` are indexed; `since`/`until`/`time_field` filter on them.
- Cron consolidation, not live session → do not install a capture plugin; have your cron job call `write`/`edit`/`forget` directly.
- Retraction → `forget(uri)` / `DELETE /api/v1/fs?uri=`, single-record and idempotent. `session.commit()` also writes a `memory_diff.json` per commit "for auditing and rollback" (`docs/en/concepts/08-session.md:169`) — though a command that actually consumes it to roll back is UNKNOWN; treat it as an audit log, not an undo button.
- Two agents, one store → your Hermes over HTTP/SDK, Claude Desktop over MCP+OAuth, same server. Peer/actor scoping exists if you later want to distinguish who wrote what.

Encouraging precedent for writing your own provider: the *other* Hermes (Nous Research) implements exactly the pattern you want — its `viking_remember` tool *"writes memory files directly, bypassing session commits or extractions"* (`docs/en/agent-integrations/16-capability-reference.md:642`). Someone else has already concluded that deliberate verbatim writes beat LLM extraction, and built it on this API. That is the shape your Hermes plugin should copy.

---

## 5. Mismatch risks, stated bluntly

1. **The default retrieval tier is the abstract, not the file.** For `entities`/`preferences`/`experiences` the docs say the writer "stores the whole body in the abstract scalar, so abstract already is the complete file" — fine. But for `resources` the abstract is a generated 256-char summary, and for `events` the default is the `# Summary` section only. **If you put a Notion page id in a long `events` record and retrieve with defaults, you may get a summary that omits the id.** Mitigation is real but you must apply it deliberately: pin `detail: "full"`, or use `find(read_content=true)`, or treat every hit as a pointer and always `read` the URI. Your "a memory hit is a POINTER — the agent then goes and reads" rule already does the right thing, but it now has to apply to OpenViking records too, not just Notion pages.

2. **🚨 The biggest risk: automatic extraction can DELETE or MERGE your hand-written records.** This is the one finding that should change how you deploy it. The memory updater applies `delete` operations by URI, and the documented candidate flow is *"Create candidate memory (**optionally delete conflicting existing memories first**)"* (`docs/en/concepts/08-session.md:154-167`; implementation `openviking/session/memory/memory_updater.py:1009-1018`). **I found no provenance check that shields hand-authored files from this.** Whether curated records are exempt is UNKNOWN — and "unknown" here means "assume not." An LLM deciding your carefully written dated assertion "conflicts" with something it just inferred, and deleting it, is precisely the class of silent corruption you are trying to escape. Mitigations, in order of safety: (a) disable extraction entirely (`memory_policy` with `self.enabled:false`, `peer.enabled:false`), (b) keep curated records **outside** `memories/` — e.g. `viking://~/notes/` — which costs you the memory-category quotas in context mode but puts them out of the updater's reach, (c) rely on the per-commit `memory_diff.json` audit trail (note: the artifact exists; a restore verb that consumes it is UNKNOWN).

3. **Write-time LLM paraphrase is still available to burn you — just not on the read path.** `remember(messages)` is **not** a verbatim write: it fabricates a throwaway session (`session_id = f"mcp-store-{uuid4()}"`) and commits it for LLM extraction (`mcp_endpoint.py:1028-1048`). **The discipline this demands: use `write`, never `remember`.** Nothing enforces that, and an agent holding the `remember` tool will eventually call it. Do not expose `remember` to Claude Desktop.

4. **Retrieval scoping is include-only.** There is no "exclude this subtree" filter for `find`/`search` — `exclude_uri` exists on `grep` alone. So "everything except the auto-extracted stuff" has to be expressed as an allow-list of `target_uri`s that you pass on *every* query. Forget once and you are reading whatever the extractor wrote. This argues strongly for putting curated records under their own subtree and always scoping to it.

5. **Pre-1.0 API churn is not hypothetical — nearly every release ships a "Compatibility and Migration" section.** A sample: **v0.4.21** made 14 context-only params *raise errors* in default `mode="list"` instead of being ignored, and changed the Docker working directory to `/app/.openviking`; **v0.4.20** retired Compile endpoints and switched extraction to a restricted Python DSL (directly causing open bug [#5404](https://github.com/volcengine/OpenViking/issues/5404)); **v0.4.18** introduced resource ACLs with two breaking changes (ACL is off by default); **v0.4.14** *removed the Qdrant and openGauss vector backends* outright; **v0.4.17** forced URI migration to `viking://~/...`. Three more breaking items sit unreleased on `main`, including peer identity moving from cwd to git origin — **which changes where memories land.** A local vector record format change is **one-way: "older releases cannot read the new record format. To downgrade, restore a backup."** Note also that the in-repo changelog is stale — it stops at v0.4.9; v0.4.10–v0.4.21 notes exist only in GitHub Releases.

6. **Coding-agent gravity.** The presets, benchmarks, plugins, and docs are overwhelmingly about coding assistants. Personal life-management is not a tested path. Nothing blocks it, but you will be the one finding the rough edges.

7. **AGPL-3.0.** Fine for a personal self-hosted setup. Worth knowing if this ever touches anything you distribute or offer as a service.

8. **Vendor gravity.** It is a ByteDance/Volcengine project with a paid hosted tier. The open-source build genuinely works without activation and without a Volcengine key (Ollama/OpenAI configs are first-class), but defaults throughout the docs point at Doubao models and VikingDB, and much of the concepts documentation still describes rerank as Volcengine-only. **Correction on that point:** the configuration guide shows rerank also accepts `provider: "openai"` with an arbitrary `api_base` (DashScope/`qwen3-rerank` is the worked example), plus Cohere and a `jev` provider routable through Vercel AI Gateway (`docs/en/guides/01-configuration.md:992-1070`). So reranking is *not* locked to Volcengine. If rerank is unconfigured or fails, retrieval falls back to raw vector scores rather than erroring.

9. **"as of date X" is a filter, not a temporal model.** You get `since`/`until` over `created_at`/`updated_at`, plus Git snapshots of the whole tree. There is no per-record bitemporal "what did I believe on date X" query. Your append-only + most-recent-wins convention has to be enforced by your own write discipline and file naming, not by the store.

10. **Arbitrary metadata is thinner than it looks.** Tags are strict `k=v` strings, AND-combined, set via `attrs set-tags`; there is a `filter` dict for metadata. This is adequate for your use case but it is not a rich document-metadata model.

---

## 6. Maturity and risk of adopting it

| Fact | Value | Source |
|---|---|---|
| Stars / forks / watchers | **38,724 / 3,020 / 109** | GitHub API, 2026-09-26 |
| Open issues + PRs | **684** (of which **208** are issues) | GitHub API / issue search |
| Repo created | **2026-01-05**; last push 2026-09-26 | GitHub API |
| Latest tag | **v0.4.21**, 2026-09-18 | `git ls-remote --tags` |
| Latest commit seen | `a09a9d2`, 2026-09-26 | local clone |
| First changelog entry | **v0.1.9, 2026-02-05** — project is ~8 months old | `docs/en/about/02-changelog.md` |
| Contributors | **281** | GitHub contributors API |
| Open PRs | **476** — many AI-agent-authored (their PR template has a "generated entirely by AI agents, no human in the loop" checkbox, and it is being ticked) | GitHub API |
| Releases in changelog | **49**; **17 core server releases in the last 3 months alone** | ibid. / GitHub Releases |
| Cadence | **roughly one release every 5 days** (v0.4.6 2026-06-29 → v0.4.21 2026-09-20) | GitHub Releases |
| Versioning | **pre-1.0** | ibid. |
| Licence | AGPL-3.0 (main), Apache-2.0 (`examples/`, `crates/ov_cli`) | `LICENSE`, `faq.md:404` |
| Self-host vs hosted | Self-host is **not activation-gated**; AGPL, "no activation required". Hosted SaaS and BYOC tiers exist alongside. | `openviking.ai`, repo |
| Phone-home | None found. "Operation telemetry" is an **opt-in response field** returned to the caller, not upstream reporting (`docs/en/guides/07-operation-telemetry.md:15`). The `usage_reporter` is a user-configured sink (you supply the endpoint). | repo docs |
| Production claim | The roadmap describes implemented capabilities without release-date commitments; docs include a deployment checklist, Helm chart, k8s manifests, OAuth, encryption, ACLs, multi-tenancy, Grafana/Prometheus. | `docs/en/about/03-roadmap.md`, `deploy/helm/` |

### What real users are complaining about

Where the complaints live: **the GitHub issue tracker.** Reddit has essentially zero footprint (nothing relevant in r/LocalLLaMA, r/ClaudeAI, r/mcp, r/selfhosted); HN mentions are warm but shallow; Chinese-language coverage is uniformly promotional. Several *issues* are written in Chinese — that is the real second channel. Absence of Reddit chatter is absence of a channel, not absence of problems.

**Declared-but-unwired configuration** *(the class that should worry you most)*
- [#5382](https://github.com/volcengine/OpenViking/issues/5382) — `skipSubagentSessions` declared, nothing reads it. Plus the substantive bug: subagent prompts stored as the human's preferences.
- [#5402](https://github.com/volcengine/OpenViking/issues/5402) — `recallExcludeUris` declared and documented, never passed to search, in the Claude Code and Codex plugins. Observed effect: 4 of 10 auto-recalled entries were boilerplate directory files, silently eating the recall budget.

**Silent data loss and corruption** *(several open, filed the same day as this research)*
- [#5403](https://github.com/volcengine/OpenViking/issues/5403) — `ov snapshot commit` broken since v0.4.19; a user's nightly backup silently failing since upgrade.
- [#5406](https://github.com/volcengine/OpenViking/issues/5406) — duplicate ACL records ⇒ deterministic 500 on every non-admin read of a URI *and all its ancestors*, with **no API recovery path**; fix means stopping the container and rewriting RocksDB. Admin-key health probes return 200, so the deployment looks healthy.
- [#3508](https://github.com/volcengine/OpenViking/issues/3508) — extraction silently drops all but the first memory when the LLM returns a JSON list (`parsed_data = parsed_data[0]`). Regression of an earlier fix. Hits llama.cpp and non-OpenAI backends.
- [#5385](https://github.com/volcengine/OpenViking/issues/5385) — directory index records unregenerable after a crash, permanently blocking `ov backup --include-vectors`; a zombie reindex task starved session_commit for an hour; index view corruption 7× in 48h.
- [#5404](https://github.com/volcengine/OpenViking/issues/5404) — the v0.4.20 restricted DSL rejects `content.edit()`, so memories cannot self-maintain; experiences stay bloated.
- **v0.4.15** was a hotfix for an unpinned `xxhash` that made **newly generated vectors silently fail to persist while the write path reported success.**
- Also: 24h of dead memory capture with no alert, messages lost at compaction, deleted trees reappearing, account backup silently omitting 12 files.

**Token burn** *(best-evidenced cluster)*
- [#5243](https://github.com/volcengine/OpenViking/issues/5243) — **~37M tokens in 3 hours** (≈5× normal daily usage) plus 1,957 HTTP 400s; oversized prompts failed *and still billed*.
- [#3226](https://github.com/volcengine/OpenViking/issues/3226) (open since July) — `compressor_v2` has no prompt budget; a 52,794-token single request; a **CUDA OOM that killed the serving engine**. Three PRs closed without resolving it.
- [#5235](https://github.com/volcengine/OpenViking/issues/5235) — dynamic content placed before the transcript ⇒ ~100% prompt-cache miss, "constantly doing fresh 500k+ reads".
- [#5379](https://github.com/volcengine/OpenViking/issues/5379) — each subagent treated as a top-level session ⇒ ~300% daily VLM spend increase.

**Retrieval quality**
- [#1258](https://github.com/volcengine/OpenViking/issues/1258) (34 comments) — a user reproducing LoCoMo got **7.66% accuracy (118/1540)** against the documented ~80%. **No independent third-party reproduction of the headline benchmarks exists**, and the published benchmark ran on **v0.3.22 — about 10 releases behind current.**
- [#204](https://github.com/volcengine/OpenViking/issues/204) — non-deterministic results for identical queries.
- [#4224](https://github.com/volcengine/OpenViking/issues/4224) — recalls nothing with a logit-scale reranker (threshold clamped to [0,1]).
- Independent review (Wavect, rev. 2026-09-17): directory summaries **lag behind source changes** — *"a successful summary read is not proof of freshness"*; and the project's own 91% token-reduction arithmetic doesn't quite add up (implies ~90.47%).

**Also**: pgvector is an open feature request, i.e. unsupported; building from source needs Rust + a C++ compiler; **both a VLM and an embedding model are mandatory — there is no embedding-only setup**.

**The pattern that matters: the dominant failure mode is *silent* failure.** Dropped memories, dropped messages, dropped backup files, vectors that never persist while the task reports success, a retrieval metric pinned at 0, and config knobs that are declared but never read. Several were only found by users reading the source. For a memory store whose entire job is to be trustworthy when you are not looking at it, that is the worst possible signature — and it is the direct reason for the "wait" verdict rather than "trial it."

**Assessment.** The engineering is unusually thorough for an eight-month-old project — the documentation is dense and specific (it documents failure modes, fallbacks, degradation ladders, and one-way format migrations, which is not what vapourware looks like), and the operational surface (Helm, OAuth 2.1, encryption, ACL, snapshots, multi-write storage) is what you would expect from a team shipping a commercial hosted tier off the same code. That is the strongest evidence for taking it seriously.

Against that: **the project does not claim production readiness, and its own documents say so.** The roadmap commits to no dates; the deployment checklist states *"a running container or a successful health probe does not demonstrate that the deployment can handle your workload"*; the desktop app is Beta; there is no "GA" or "stable" wording anywhere in the README, FAQ or roadmap. Evidence offered is academic (three papers, a self-run benchmark on v0.3.22) rather than operational. The only substantive independent review concludes: *"Is it production-ready by default? No."* Blog posts calling it "the first production-ready solution for AI agent memory" are vendor-adjacent marketing, not user testimony.

Meanwhile 476 open PRs — a large share AI-authored with no human in the loop — against 281 contributors and a release every 5 days describes a project adding surface area faster than it is hardening it. The recurring root causes around indexing, locking and semantic processing persist from v0.4.7 through v0.4.21.

**Recommendation: do not adopt now. Re-evaluate in 2–3 months or at 1.0.**

You would be moving off a provider that silently gave you wrong answers onto one whose signature failure mode is *silence* — and whose backup mechanism is currently broken. That is a lateral move, not an escape. The architecture is right; give the implementation two or three more months.

**If you want to evaluate anyway, run these in order and stop at the first failure:**

1. **Check whether the blockers have cleared** — issues [#5402](https://github.com/volcengine/OpenViking/issues/5402), [#5382](https://github.com/volcengine/OpenViking/issues/5382), [#5403](https://github.com/volcengine/OpenViking/issues/5403), [#5406](https://github.com/volcengine/OpenViking/issues/5406). If `ov snapshot` is still broken, stop: you have no backup, so nothing below is safe to trust with real data.
2. Stand up the container with Ollama or OpenRouter embeddings — proves requirement 1 on your hardware. Remember **both** a VLM and an embedding model are mandatory.
3. **The verbatim test.** `write` a record containing a real Notion UUID; then `grep` it, `find` with a short query and `read_content=true`, and `read` the URI. **Confirm byte-identical every time.** This is the test Cognee failed. If it fails, stop.
4. **The deletion test.** Hand-write a record under `viking://~/memories/events/`, then commit a session that contradicts it. See whether your file survives.
5. **Verify every off-switch empirically rather than trusting the schema** — this project demonstrably ships knobs that do nothing. Set it, then prove the behaviour actually changed.
6. Connect Claude Desktop over OAuth; confirm you can withhold `remember`.
7. Restart, confirm persistence, then attempt a snapshot **and** a restore.

Steps 1, 3 and 5 are the decision. Everything else is ergonomics.

---

## 7. Sources

Primary (in-repo, commit `a09a9d2`, 2026-09-26):
- `README.md`, `docker-compose.yml`, `LICENSE`, `examples/ov.conf.example`
- `docs/en/concepts/05-storage.md` — dual-layer storage, vector schema, hybrid index
- `docs/en/concepts/07-retrieval.md` — retrieval pipeline, `find()` vs `search()`, score propagation
- `docs/en/concepts/06-extraction.md`, `docs/en/concepts/08-session.md` — commit-triggered extraction, `memory_diff.json`
- `docs/en/api/06-retrieval.md` — `rewrite` default, `rendered`/`digest`, tier table, `grep` params, `since`/`until`/`tags`
- `docs/en/api/16-memory.md` — built-in memory types
- `docs/en/api/03-filesystem.md` — `attrs`, `set_tags`
- `docs/en/api/11-snapshot.md` — Git-backed snapshots
- `docs/en/guides/01-configuration.md` — `embedding`/`vlm` config, `memory_policy` defaults
- `docs/en/guides/06-mcp-integration.md` — the 16 MCP tools, OAuth for Claude Desktop
- `docs/en/guides/07-operation-telemetry.md` — telemetry is opt-in and caller-facing
- `docs/en/about/02-changelog.md` — 49 releases, breaking changes
- `docs/en/agent-integrations/05-hermes.md` — the Nous Research Hermes (⚠️ not yours)
- `examples/claude-code-memory-plugin/README.md` — capture off-switches, `OPENVIKING_AUTO_CAPTURE`
- `docs/en/api/14-ovpack.md:160` — session files are not vectorised
- `docs/design/session-memory-extraction-flow.md` — `memory_policy` shape

Code read directly (same checkout):
- `openviking/server/mcp_endpoint.py:716-722, 1028-1048, 1053-1090, 1903-1914` — `read`, `remember`, `write`, `forget`
- `openviking/server/routers/search.py:256-281, 312, 347-390` — `read_content`, `rewrite=False` default, `find` has no LLM
- `openviking/service/fs_service.py:1070-1086` + `session/memory/utils/content_visibility.py:16-21` — verbatim read semantics
- `openviking/session/memory/memory_updater.py:1009-1018` + `session/session.py:216-227` — **extraction deletes by URI**; policy gating
- `openviking/storage/collection_schemas.py:93-170` — vector record schema and scalar indexes
- `openviking/storage/viking_fs/_grep.py:52, 388-391` — grep BM25 recall above 10k files
- `openviking/retrieve/hierarchical_retriever.py:150-184` — dense + sparse query vectors

Web:
- https://github.com/volcengine/OpenViking — repo, GitHub API stats (2026-09-26)
- https://github.com/volcengine/OpenViking/issues?q=is%3Aissue+is%3Aopen+sort%3Acomments-desc — open issue themes
- https://openviking.ai/
- https://developers.redhat.com/articles/2026/04/23/deploy-openviking-openshift-ai-improve-ai-agent-memory
- https://wavect.io/blog/openviking-agent-memory-review/
- https://faun.pub/openviking-explained-reinventing-memory-and-context-for-ai-agents-c189b2bea61b

**Marked UNKNOWN / not verified:** hosted SaaS tier limits and pricing (first-party page is JS-rendered and not fetchable; the "~50 file" free-tier cap comes from secondary review sites only); whether OpenTelemetry span data actually leaves a given host (operator-dependent — but note [#3952](https://github.com/volcengine/OpenViking/issues/3952), where request/response payloads were used as span *event names*, leaking prompt content into telemetry at high cardinality; fix PR still open); real-world RAM/CPU footprint; how [#1258](https://github.com/volcengine/OpenViking/issues/1258) (the 7.66% benchmark repro) was actually resolved; whether hand-authored memory files are shielded from extraction-driven deletion; whether any command consumes `memory_diff.json` to roll back.

**One data-integrity caveat on my own research:** a `list_releases` API call returned rows belonging to a *different* repository (`vectorize-io/hindsight`, v0.9.x/v0.10.x). Those were excluded. If a "v0.10.x" version is ever attributed to OpenViking downstream of this work, that is the contamination, not real — OpenViking is on 0.4.x.

**Licence note:** AGPL-3.0's network clause is flagged repeatedly by reviewers as the main adoption friction — a modified server exposed over a network, including an internal SaaS-style deployment, carries source obligations. For a single-user self-hosted setup this is a non-issue.

**Commercial edition:** a licence key and "offline telemetry arrangements" appear only in the *Self-Managed / private delivery* product (`docs/en/guides/19-deployment-checklist.md`), which adds distributed deployment and support. The OSS server needs no activation key. Notably the gating runs the *other* way in one case: [#4175](https://github.com/volcengine/OpenViking/issues/4175) reports the **hosted** version blocking `/api/v1/privacy-configs`, an API the OSS build exposes.

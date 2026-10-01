# Why cognee's extractor never sees existing entities, and what it offers instead

**Date:** 2026-09-29 · **Depth:** standard (3 hops) · **Confidence:** high on
what exists, medium on why
**Question:** Why does graph extraction not show the LLM the existing graph
nodes, and what does cognee offer (or plan) for cross-document entity
resolution?

**Sources:** cognee 1.6.1 source read read-only from the running backend
(`cognee-backend-lndyf8z46p75oh524khm5z19:/app/cognee`, reports
`1.6.1-local`), docs.cognee.ai, and topoteretes/cognee issues, PRs and
commits. Where the source and the docs disagree, the source wins, because it
is the code that runs. Nothing was written to the live system.

**Correction to the 2026-09-25 report:** that report said of `ameer` vs
`ameer akashe`: "no alias mechanism exists … **no — not in 1.6.0**". That is
wrong for 1.6.1. A post-hoc merge pipeline, `consolidate_entities`, ships in
1.6.1 and can be called over HTTP (§2.1). No alias mechanism exists at write
time. That part still holds.

---

## Executive summary

| question | answer | basis |
|---|---|---|
| Does cognee state why extraction is per-chunk and blind to the graph? | **Not directly.** No doc, docstring or issue gives parallelism, cost or scale as the reason. The closest things to a stated rationale are (a) the "dedup contract": same normalised name ⇒ same id ⇒ same node, and (b) the docs' claim that the extractor resolves aliases when both mentions are in one ingest. | §1 |
| Does 1.6.1 have anything that merges aliases? | **Yes, after the fact, and it is destructive:** the `consolidate_entities` memify pipeline (name-embedding cosine ≥ 0.85 plus normalised-name match, same EntityType only). It is not part of `improve()`. It keeps no alias record, so the next write of `ameer` re-creates the duplicate. | §2 |
| Would it merge `ameer` / `ameer akashe`? | **Unknown, possibly not.** The normalised-name pass cannot match them (`ameer` ≠ `ameerakashe`). The cosine pass needs ≥ 0.85 on the live embedding model, and that was not measured. | §2.1 |
| Is write-time resolution planned? | **Not currently.** Five hackathon approaches were closed on 2026-08-09. The in-cognify LLM-judge PR #4106 was closed unmerged. The maintainers deleted the prefetch POC on the grounds that ontology plus `consolidate_entities` "shipped solutions". A user asked on 2026-09-08 whether it is still on the roadmap and has had no answer. | §2.3 |
| Has anyone injected existing entity names into the extraction prompt? | **Yes, cognee itself, twice.** (1) The `prefetch_disambiguation` POC appended the top-k nearest existing entity names to `custom_prompt` for each chunk. It was deleted on 2026-08-12. (2) **Live in 1.6.1:** session distillation passes an "ENTITY GLOSSARY" of the 20 nearest `Entity_name` hits to its writer LLM. | §3 |
| Comparable systems | Graphiti and Mem0 do read-before-write resolution with LLM judgement. GraphRAG and LightRAG merge on exact name only, as cognee does. | §3.3 |
| Guidance for short, independently written records | **None directly.** The docs' dedup guide describes exactly this case (texts ingested separately) and points to `consolidate_entities`. | §4 |

**Single most useful finding:** our write pattern (one record = one chunk =
one `/remember` call) makes the removed prefetch POC reproducible with **no
server change**. Before each write, query the `Entity_name` collection with
the record text, then send the full default graph prompt plus an "Existing
entities:" list as `custom_prompt`. cognee's own session distiller does the
same kind of anchoring today (`modules/session_distillation/distill.py:360-375`).
One catch: `custom_prompt` *replaces* the system prompt
(`extract_content_graph.py:21-22`). It is not appended, so the full default
prompt text must travel with every call.

---

## 1. Stated rationale

### 1.1 What cognee states

**The dedup contract.** Identity is the name, by design:

> "cognee's dedup contract is that two nodes with the same identity value are
> the *same* node because they hash to the same id (COG-2515)"
> — `api/v1/validate/validate.py:19-21`

> "identity_fields makes the id deterministic and namespaced by class
> (`Entity:<name>`) when constructed without an explicit id … Prevents the
> random-uuid4 footgun." — `modules/engine/models/Entity.py:18-21`

> "The identity of the stored node is derived from its `name` … so every
> mention of the same name — across chunks, across documents, and across
> later `.cognify` runs — resolves to the same `Entity` node instead of
> creating a duplicate."
> — [docs: Cognify](https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/cognify)

The current id is `uuid5(NAMESPACE_OID, f"Entity:{normalised}")`, where
normalisation is `lower().replace(" ","_").replace("'","")`
(`infrastructure/engine/models/DataPoint.py:164-193`). This replaced the bare
`generate_node_id` scheme after `Entity("institution")` and
`EntityType("institution")` collided (`modules/migrations/versions/namespace_entity_type_node_ids.py:1-20`,
[#2510](https://github.com/topoteretes/cognee/issues/2510)). The brief's
`generate_node_id` description is still correct for the normalisation. The
docstring says it is "kept byte-for-byte aligned" with the old scheme
(`DataPoint.py:167-170`).

**Aliases resolve only within one ingest.** The dedup guide says:

> "when the texts are ingested together, the LLM sees both mentions at once,
> resolves the abbreviation during extraction, and emits a single entity,
> leaving nothing to merge."
> — [docs: Entity Deduplication](https://docs.cognee.ai/guides/memify-entity-deduplication)

The default prompt's only coreference instruction is scoped to the text in
front of the model: "If an entity is mentioned multiple times in the text …
always use the most complete identifier"
(`infrastructure/llm/prompts/generate_graph_prompt.txt:28-32`).

**Known limitation, acknowledged:** "Because the id is derived from the name
they never collapse on their own" — the PR that added `consolidate_entities`
([#3534](https://github.com/topoteretes/cognee/pull/3534)).

**Why the prefetch approach was dropped** (the nearest thing to a stated
position on graph-aware extraction):

> "Relevance audit: none of the three POC approaches shipped to core, and
> their shared problem (entity duplication/aliasing) has shipped solutions
> that arrived by other routes - ontology canonicalization in
> extract_graph_from_data and the consolidate_entities memify pipeline."
> — commit [c332e75](https://github.com/topoteretes/cognee/commit/c332e7560b59866ab63e419387a091ea007e2697), 2026-08-12

### 1.2 What cognee does not state

I found **no** doc, docstring, issue or PR giving parallelism, cost, scale,
determinism of LLM output, or avoiding a graph read as the reason extraction
does not consult the graph. Only the pieces above exist.

### 1.3 Inferred (not stated)

- **Parallelism is real but not decisive.** `extract_graph_from_data` fans
  all chunks out with `asyncio.gather` (`tasks/graph/extract_graph_from_data.py:225-229`).
  A per-chunk vector lookup would not break that. The POC did its lookup
  inside the same gather.
- **"Avoid a read before write" is not a principle cognee holds.** The same
  task already reads storage before writing, to skip existing edges
  (`find_existing_edge_identities`, `extract_graph_from_data.py:103`,
  `modules/graph/utils/retrieve_existing_edges.py:16`). So the absence of an
  entity lookup is a choice, not an architectural rule.
- **The likely reason is idempotency and order-independence.** Name-hash ids
  give the same graph regardless of ingest order, and a re-cognify is a
  no-op. Prompt-injected candidates make the output depend on what was
  written earlier. A community reviewer listed "results remain stable when
  document order changes" as an acceptance test for any resolver
  ([#3628 comment](https://github.com/topoteretes/cognee/issues/3628#issuecomment-5060539538)).
  The maintainers have not said this.
- **cognee's direction is "extract dumb, fix later".** It has shipped
  post-hoc graph surgery (`consolidate_entities`, `cross_connect_entities`,
  `consolidate_entity_descriptions`) and removed extraction-time resolution
  twice (the POC and PR #4106).

---

## 2. Entity resolution that exists or is planned

### 2.1 `consolidate_entities`: in 1.6.1, post-hoc, destructive

**Code:** `tasks/memify/consolidate_entities.py` (563 lines) and
`memify_pipelines/consolidate_entities.py`. Merged in
[#3534](https://github.com/topoteretes/cognee/pull/3534) on 2026-07-25.
Documented at [docs: Entity Deduplication](https://docs.cognee.ai/guides/memify-entity-deduplication).

**How it works:**
- It loads every `Entity`, embeds each name, and unions pairs whose cosine
  similarity is ≥ `similarity_threshold` (default **0.85**) among each
  node's `top_k=10` neighbours (`consolidate_entities.py:41-57,176-178`).
- A second pass unions names that are equal after
  `re.sub(r"[^a-z0-9]+","", name.lower())` (`:69-73,180-187`).
- Only entities of the **same EntityType** are merged unless
  `allow_cross_type=True` (`:169-170`).
- The canonical is the node with the highest degree, then the oldest
  `created_at`, then the lowest name (`:312-328`).
- Edges are re-pointed onto the canonical, descriptions and
  `belongs_to_set` are unioned, and the duplicate nodes and their
  `Entity_name` vectors are deleted (`:450-550`).

**How to call it:**
- **HTTP:** `POST /api/v1/memify` with `extractionTasks:["detect_entity_duplicates"]`
  and `enrichmentTasks:["merge_entity_duplicates"]`
  (`api/v1/memify/routers/get_memify_router.py:57-63`,
  `memify_pipelines/memify_task_registry.py:49-50`).
- **Caution:** the HTTP path builds the tasks with **no config**
  (`memify_task_registry.py:49-50`), so it runs `DEFAULT_CONFIG`:
  `dry_run=False`, threshold 0.85. **There is no HTTP dry run.** The docs'
  advice to "Run it with `dry_run=True` first" applies to the SDK only.
- **SDK:** `consolidate_entities_pipeline(similarity_threshold=…, dry_run=True, protect_node_types=[…])`
  (`memify_pipelines/consolidate_entities.py:25-35`).

**Would it merge `ameer` / `ameer akashe`?**
- The name-match pass: **no.** `ameer` ≠ `ameerakashe`.
- The cosine pass: **only if** the two names score ≥ 0.85 on the live
  embedding model and both carry the same EntityType (e.g. `person`). Neither
  was measured. Measuring needs an embedding call, which was out of scope
  for a read-only session. **Gap.**

**Inferred problems (from the code, not stated anywhere):**
- **The merge does not stick.** `merged_from` is only logged
  (`consolidate_entities.py:486-503`). No alias is persisted. The next record
  that extracts `ameer` hashes to `Entity:ameer` again. If that node was
  deleted as a duplicate, it comes back. The pipeline has to be re-run on a
  schedule.
- **False merges.** A production user reports that the 0.85 default "merged
  the wrong things 30–46% of the time": ticket ids, `cognee_remember` /
  `cognee_recall`, negations, "and every date". Exact-name mode was safe
  ([#4106 comment](https://github.com/topoteretes/cognee/pull/4106#issuecomment-5586625288)).
  Our graph has date entities and short tool names, so this risk applies to us.
- **Scale edge.** It embeds every entity name in one call
  (`consolidate_entities.py:266`). Open PR
  [#4954](https://github.com/topoteretes/cognee/pull/4954) reports this fails
  above 2,048 inputs on OpenAI. Our graph has 3,286 entities
  (`docs/cognee-graph-analysis.md:24`). Our provider is `custom`, so whether
  this bites is **unverified**.
- **Not affected:** the Neo4j metadata bug
  ([#4953](https://github.com/topoteretes/cognee/pull/4953)). We run
  `GRAPH_DATABASE_PROVIDER=kuzu`.

**Planned:** an opt-in LLM judge for this pipeline, open PR
[#5054](https://github.com/topoteretes/cognee/pull/5054). It was not
reviewed as of 2026-09-21 ("a big review backlog"). The author reports it cut
wrong-digit merges from 78 to 0 on a 4,804-entity graph.

### 2.2 The `improve()` stages: none consolidate entities

These are the nine stages in `modules/improve/stages.py`:

| stage | does | merges aliases? | source |
|---|---|---|---|
| feedback_weights | moves `feedback_weight` on used elements | no | `stages.py:80-83` |
| persist_session_qa / persist_agent_traces | cognify session Q&A and traces | no | `:110-142` |
| extract_agent_context / distill_sessions | agent lessons and session learnings | no (but see §3.1) | `:168-237` |
| update_user_preferences | `prefers` subgraph | no | `:285-288` |
| build_truth_subspace (opt-in) | truth subspace from learnings | no | `:331-334` |
| triplet_enrichment | "default memify enrichment (triplet embeddings)" | no | `:378-390` |
| global_context_index (opt-in) | bucket and root summaries | no | `:458-461` |

`consolidate_entities` is not a stage. It must be invoked on its own.

### 2.3 Other 1.6.1 pieces and the roadmap

- **`cross_connect_entities`** (`tasks/memify/cross_connect_entities.py`)
  proposes *new edges* between related but unlinked entities, using vector
  and neighbour overlap plus an LLM check. It "never creates, rewrites, or
  deletes a node" ([docs](https://docs.cognee.ai/guides/memify-entity-deduplication)).
  It does not merge aliases and is SDK-only (absent from the HTTP registry).
- **`consolidate_entity_descriptions`** rewrites descriptions only. It does
  not merge.
- **The ontology resolver** was covered in the 2026-09-25 report: a difflib
  0.8 cutoff and no label support. It will not match our names.
- **Hackathon tickets.** Five approaches were opened on 2026-06-28 and all
  closed on 2026-08-09: A deterministic
  ([#3627](https://github.com/topoteretes/cognee/issues/3627)), B embeddings
  (#3628), C LLM judge
  ([#3629](https://github.com/topoteretes/cognee/issues/3629)), D graph
  structure ([#3630](https://github.com/topoteretes/cognee/issues/3630)),
  E temporal. Approach C became PR
  [#4106](https://github.com/topoteretes/cognee/pull/4106): opt-in
  `ENTITY_CANONICALIZATION`, running inside cognify between extraction and
  storage. It was **closed unmerged on 2026-08-08, with no stated reason**.
  Its own scope note: "Blocking is within the current cognify batch (no
  cross-batch/cross-run dedup)". So it would not have helped one-record
  writes either.
- **Open question upstream.** "is entity resolution at write time still on
  the roadmap?" was asked on
  [#4106](https://github.com/topoteretes/cognee/pull/4106#issuecomment-5586625288)
  on 2026-09-08. It has no maintainer reply. A search for open issues on
  write-time entity resolution returned none.

---

## 3. Injecting existing entity names into the prompt

### 3.1 Precedent inside cognee

**`prefetch_disambiguation` POC** (deleted in c332e75; last present at
[1ebe0c6](https://github.com/topoteretes/cognee/blob/1ebe0c621645d5e1d0529d24f0118136d58bb36b/examples/pocs/prefetch_disambiguation/prefetch_disambiguation.py)):
- It embeds each chunk, takes the top-k (default 5) cached entity names by
  cosine, and appends them to `custom_prompt` as `"\n  -" + name` (`:84-92,129-141`).
- The prompt tail it used (`prompts/prompt1.txt`) says: "# 5. Reuse names
  (alias-aware) Prefer existing entities. If a mention is an alias,
  abbreviation, misspelling, or naming variant of an existing entity, map it
  to that existing name instead of creating a new one … Existing entities:".
- It plugged in through the `calculate_chunk_graphs` kwarg hook, which
  **still exists in 1.6.1** (`tasks/graph/extract_graph_from_data.py:219-222`,
  SDK-only).
- No evaluation numbers were published. PR
  [#2346](https://github.com/topoteretes/cognee/pull/2346) ("Disambiguation
  POC quality analysis") has no human-written results.

**Session distillation glossary (live in 1.6.1):**
- The writer prompt receives "ENTITY GLOSSARY: exact names of entities that
  already exist in the knowledge graph" and is told to "never paraphrase,
  shorten, or rename a glossary entity" (`infrastructure/llm/prompts/session_distillation_writer_system.txt:5,14`).
- The glossary is the top 20 `Entity_name` hits for the lesson text
  (`modules/session_distillation/models.py:42`, `distill.py:360-375`).
- This is anchoring *upstream* of extraction: the lesson text uses canonical
  names, so the later name-hash extraction lands on existing nodes.

**`custom_prompt_generation_system.txt:25-33`** (used by the `/llm` router,
`get_llm_router.py:238`) tells generated prompts to keep a *document-level*
entity registry. That is intra-document only.

### 3.2 Mechanics for us (inferred from code)

- `/api/v1/remember` takes `custom_prompt` per call
  (`get_remember_router.py:214,606`).
- `extract_content_graph` then uses it **as the whole system prompt**
  (`extract_content_graph.py:21-22`). Each call must therefore carry the full
  default prompt text plus the candidate list.
- One record is one chunk, so a per-call candidate list is exactly the POC's
  per-chunk list.
- Candidates can come from a nearest-neighbour query on the `Entity_name`
  collection (in pgvector here). That is the same collection the distiller
  and `consolidate_entities` use. Which HTTP route exposes it cleanly was not
  checked. **Gap.**

**Trade-offs (my synthesis, not measured):**

| trade-off | detail |
|---|---|
| cost | one extra embedding and vector query per write, plus roughly 5–20 names of prompt |
| idempotency | re-cognifying a record can now produce different names, because the output depends on graph state |
| error propagation | a wrong candidate pulls a mention onto the wrong node, and the name-hash id makes that silent |
| type drift | the LLM may reuse the name but assign a different EntityType, leaving a second type edge |
| complementarity | it prevents new duplicates; `consolidate_entities` (exact-name mode is the safe setting per #4106) cleans up the backlog |

### 3.3 Comparable systems (design patterns only)

| system | pattern | source |
|---|---|---|
| Graphiti / Zep | extract with the last n=4 messages as context. Then, per entity, find candidates by embedding cosine and full-text search, and let an LLM decide "duplicate of uuid X" and rewrite name and summary. This is read-before-write, per entity. | [Zep paper §2.2.1, App. 6.1.2](https://arxiv.org/html/2501.13956v1) |
| Mem0 (current OSS) | nearby existing memories go into the extraction prompt as `existing_memories`. After that, only exact-hash dedup. | [mem0 discussion #4787](https://github.com/mem0ai/mem0/discussions/4787) |
| Mem0 (paper, graph variant) | per new triple, embedding search for existing nodes above a threshold *t*, then reuse or create | [arXiv 2504.19413](https://arxiv.org/html/2504.19413v1) |
| Microsoft GraphRAG | per-chunk extraction. Entities with the same title and type merge, and an LLM summarises their descriptions. No alias resolution. | [GraphRAG dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/) |
| LightRAG | per-chunk extraction with exact-name merge and description summarisation (same as cognee) | [arXiv 2410.05779](https://arxiv.org/abs/2410.05779) (not re-read this session) |

**Pattern.** Batch-document systems (GraphRAG, LightRAG, cognee) accept
exact-name merging, because one large document gives the extractor enough
context to name things consistently. Systems built for streams of small
episodes (Graphiti, Mem0) all read the graph before they write. Our corpus is
the second shape running on a system built for the first.

---

## 4. Guidance for short, independently written records

- **Nothing targets this directly.** The closest statement is the dedup
  guide's contrast between texts ingested together, which resolve at
  extraction, and texts ingested separately, which need
  `consolidate_entities`
  ([docs](https://docs.cognee.ai/guides/memify-entity-deduplication)).
- **The best evidence comes from a deployment like ours.** MX runs cognee as
  agent memory: "Agents call `remember` when they learn something … no
  ontology and no alias list". They report that exact-name consolidation was
  safe (194 merges, 0 errors) and that the embedding threshold was not
  ([#4106 comment](https://github.com/topoteretes/cognee/pull/4106#issuecomment-5586625288)).
- **Inferred:** cognee's own answer to the short-record problem is the
  distiller glossary: write the text with canonical names before it reaches
  extraction. For Hermes, the equivalent writer is the agent that composes
  the record. That points to a cheaper option than prompt injection:
  resolve names in the record text before `/remember`, so the unchanged
  default prompt emits canonical names.

---

## Confidence and gaps

| claim | confidence | basis |
|---|---|---|
| extractor sees only chunk + system prompt | **high** | `extract_graph_from_data.py:219-229`, `extract_content_graph.py:21-22` |
| no stated parallelism/cost rationale | **medium** | searched docs, issues, PRs, docstrings; absence can't be proven |
| "dedup contract" is the stated identity rule | **high** | verbatim `validate.py:19-21`, `Entity.py:18-21`, docs |
| `consolidate_entities` is in 1.6.1 and HTTP-reachable without dry run | **high** | live source, router and registry read |
| consolidation does not stick | **high** (code) | merge report is log-only; ids are name-derived |
| `ameer` / `ameer akashe` would merge at 0.85 | **unknown** | cosine not measured on the live model |
| 2,048-input embed failure applies to us | **unverified** | provider is `custom` |
| no `improve()` stage merges entities | **high** | all nine stage docstrings read |
| write-time resolution is off the roadmap | **medium** | #4106 closed without reason, POC deleted, question unanswered |
| prefetch POC is the direct precedent | **high** | source and prompt read at 1ebe0c6 |
| POC effectiveness | **unknown** | no published numbers |
| Graphiti / Mem0 / GraphRAG patterns | **high / medium / high** | primary papers/docs; Mem0 from a discussion thread |
| LightRAG pattern | **medium** | from paper knowledge, not re-read |

**Gaps worth closing before building:**
1. Cosine of `ameer` vs `ameer akashe` (and ten other known alias pairs) on
   the live embedding model. This decides whether `consolidate_entities` is
   usable for us at all.
2. A dry run of `consolidate_entities` on a **copy** of the graph through the
   SDK. HTTP cannot dry-run.
3. Whether the `custom` embedding provider has a 2,048-input cap.

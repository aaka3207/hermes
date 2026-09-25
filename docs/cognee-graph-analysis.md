# What the Cognee graph actually built from the Mnemosyne import

Measured 2026-09-25 against the live `shared` dataset (Kuzu, read-only) and
`provenance_edge_evidence` in Postgres. This exists to inform the §11
evaluation verdict in `cognee-operations.md` with numbers rather than
impressions.

**Short version:** the synthesis quality is genuinely better than Mnemosyne's
ranked-record output. The *graph* underneath it is much weaker than the
synthesis makes it look — it is mostly a star around four junk hubs, with a
predicate vocabulary too fragmented to query. Retrieval is winning on the LLM
and the embeddings, not on the graph structure.

---

## 1. Shape

| node type | count | what it is |
|---|---|---|
| `Entity` | 3,286 | extracted subjects |
| `EntityType` | 653 | their categories |
| `TextDocument` | 1,432 | one per imported record |
| `DocumentChunk` | 1,433 | **one per document** |
| `TextSummary` | 1,433 | one per chunk |
| `NodeSet` | 2 | dataset grouping |
| **total** | **8,239** | |

**One chunk per document.** Mnemosyne records are short enough that nothing
was ever split, so there is no intra-record structure. Every relationship the
graph holds was extracted from a single short record in isolation — the LLM
never saw two records together. Any cross-record connection exists only
because the same entity name was extracted twice.

Roughly 2.3 entities per record.

## 2. Two thirds of the edges are scaffolding

Of 15,402 edges, **10,026 (65%)** are `contains` / `is_part_of` /
`belongs_to_set` — cognee's own document→chunk→entity plumbing, carrying no
information about your memories. The semantic layer is **5,130 entity→entity
edges**.

## 3. The predicate vocabulary is unusable as a query surface

**2,273 distinct predicates** across those ~5,376 semantic edges.

| edges per predicate | number of predicates |
|---|---|
| 1 | **1,609** |
| 2 | 324 |
| 3 | 101 |
| 4 | 59 |
| 5+ | ~180 |

**1,609 predicates are used exactly once.** Nothing was normalized, so one
idea is spread across many spellings:

```
recorded_at 134 · recorded_on 124 · recorded 73 · records 30 · recorded_in 10
```

Five spellings, 371 edges, one concept. Worse, the **negation family** — the
highest-stakes memories a memory system holds — is the most scattered:

```
must_not_use 39 · prohibits 33 · avoids 23 · does_not_use 6
prohibited_use 6 · must_avoid 5 · prohibits_use 5 · must_never_use 5
```

Eight predicates, 122 edges, all meaning "don't". You cannot ask this graph
"what am I forbidden to do" by traversing relationships. That question only
works through the LLM synthesis layer, which re-reads the text anyway.

Real triples show why the tail is so long:

```
ameer -requires->                  autonomy
ameer -employed_at->               livefront
ameer -redistributed_energy_i...-> therapy
ameer -career_break_extended_...-> career break
ameer -has_career_break->          career break
ameer -primary_ai_ml_sme->         anthropic select partnership
ameer -prefers_keeping->           exercise rep range
```

`requires` and `employed_at` are reusable. `redistributed_energy_into` and
`career_break_extended_duration` are sentence fragments promoted to schema —
they will never match a second edge. Note also `career_break_extended_*` and
`has_career_break` both pointing at the same node: the same fact, twice, under
two predicates.

## 4. Entities barely connect records

This is the finding that matters most, because cross-record connection is the
entire argument for a graph over plain vector search.

| records mentioning an entity | entities |
|---|---|
| **1** | **2,576 (78.4%)** |
| 2 | 369 |
| 3 | 127 |
| 4 | 67 |
| 5+ | 147 |

**78% of entities appear in exactly one record** and connect nothing.

And the hubs that do connect things are mostly noise:

| entity | records | verdict |
|---|---|---|
| `user` | 482 | generic; links everything to everything |
| `mnemosyne` | 379 | an artifact of the source system, not a topic |
| `working_memory` | 200 | Mnemosyne's internal tier name |
| `ameer akashe` | 194 | it's you — links everything |
| `ameer` | 35 | **same person, second node** |
| `2026-09-14` | 44 | a date |
| `2026-09-15` | 43 | a date |
| `2026-09-02` | 33 | a date |

Four observations:

* **`user` and `ameer akashe` are non-discriminating.** Nearly every memory is
  about you, so a hub meaning "you" carries no signal. The star topology in §3
  is this.
* **`mnemosyne` and `working_memory` are import residue.** 579 records'
  strongest connection is to the *old system's vocabulary*, because the export
  wrote tier names and provenance into the text.
* **Dates became entities.** Two unrelated memories written on 2026-09-14 are
  now 2 hops apart. These are false bridges, and contradiction detection
  works over 1-hop neighbourhoods, so they are exactly the wrong shape.
* **`ameer akashe` ≠ `ameer`.** Cognee derives entity ids deterministically
  from the name (`uuid5`), with no aliasing, so any spelling variation splits
  a node permanently.

The genuinely useful hubs are the concrete nouns: `composio_search_tools` (72),
`composio_multi_execute_tool` (67), `gbrain` (44), `notion` (37), `gmail` (32),
`dinefile` (30), `discord` (22). Tools and projects resolve well. People,
dates, and abstractions do not.

## 5. Retrieval, head to head

Same query, both systems.

### "What must never be done when deploying or writing to production?"

**Cognee** — one synthesized sentence:

> you must never use a remote shell/workbench or a local shell

**Mnemosyne** — 6 raw records, top hit about Notion database schema
(irrelevant, matched on the words "must"/"never"); the closest relevant record
ranked 6th. Every result had `dense_score: 0.0`, so the vector lane
contributed nothing and it ran on keyword/FTS alone.

Cognee wins clearly here.

### "What is dinefile and how does it relate to Hermes profiles and memory?"

**Cognee** produced a correct multi-paragraph synthesis: DineFile is the
restaurant-discovery app formerly called `recd`, on Vercel/Supabase/PostHog/
Resend/Upstash; Hermes owns the marketing-content workstream; it has its own
profile at `/opt/data/workspace/dinefile` with a reduced skill set and its own
memory on Cognee Cloud while the default profile moved from Mnemosyne to
self-hosted Cognee.

**Mnemosyne** returned 5 relevant records (dense scores healthy here — 0.96+ —
so the zero-vector behaviour above is query-specific, not systemic). But the
stack detail (Vercel, Supabase, PostHog, Resend, Upstash) appears in **none**
of its top 5. Cognee pulled from more records than Mnemosyne surfaced.

Cognee wins again — and this is the case a graph is supposed to win.

### The catch

Cognee returned **"1 memory found"** both times. It gives you an answer, not
evidence. Mnemosyne hands back per-record `timestamp`, `source`, `importance`,
`recall_count`, `veracity`, `superseded_by` and component scores — you can see
*why* something ranked and whether it is stale.

For a store where 1,380 records were imported from a two-year-old corpus and
contradiction detection **is not retroactive**, that matters: a superseded fact
and its replacement are equally eligible to be blended into one confident
paragraph, with nothing in the output to flag it.

## 6. What this means

**Keep Cognee.** The synthesis is better on both query shapes, including the
relational one, and that is what an agent consumes.

**But the graph is not doing the work you'd assume.** With 78% singleton
entities and a predicate vocabulary of 2,273 mostly-unique names, retrieval
quality is coming from the embeddings and the extraction LLM. The graph is
contributing hub expansion around a handful of real nouns (tools, projects)
and noise around the rest.

Cheap improvements, in order of value:

1. **Strip import residue before re-seeding.** `mnemosyne`, `working_memory`
   and tier vocabulary are the #2 and #3 hubs in your memory graph and mean
   nothing. They come from the export format, not your facts.
2. **Stop extracting bare dates as entities**, or the false-bridge problem
   grows with every write and pollutes the 1-hop neighbourhood that
   contradiction detection reads.
3. **Normalise the identity node.** `ameer` / `ameer akashe` / `user` should be
   one node, and arguably excluded from extraction entirely — a hub that means
   "the user" in a personal memory store is pure noise.
4. **Re-run the 91 unseeded records** (index 574, 1381–1470) so the corpus is
   whole before any verdict.

None of these need upstream changes; 1–3 are seed-time text hygiene.

## 7. Method

```bash
# graph (read-only; safe against the running backend)
docker exec <cognee-backend> python -c "import kuzu; ..." \
  # DB: /cognee-storage/system/databases/<uuid>/85761401-...pkl

# predicate distribution
select relationship_name, count(*) from provenance_edge_evidence group by 1;
```

Entity-sharing counts come from
`MATCH (d:DocumentChunk)-[EDGE]->(n:Entity)` grouped by `count(DISTINCT d)`.
`provenance_edge_evidence` is **not** gated by `PROVENANCE_TRACKING` — it
predates enabling that flag and covers the whole corpus.

# Consolidation as a Hermes job: a design sketch

**Status: thinking, not a plan.** Nothing here is scheduled or agreed. It
exists so the reasoning survives the conversation it came out of.

Measurements are from the live `shared` dataset on 2026-09-25 and are marked
where they matter. Everything else is design reasoning and should be read as
provisional.

---

## 1. The problem this is trying to solve

Cognee accretes. It does not consolidate, and nothing in it will.

`docs/cognee-graph-analysis.md` has the numbers; the mechanism is:

* the only deduplication is `deduplicate_nodes_and_edges`, which matches on
  exact node id -- and ids are `uuid5` of the name, so `ameer` and
  `ameer akashe` can never merge;
* `improve_on_end` runs nine stages, seven of which are session-fed and skip
  with `no_session_ids` (Hermes sends none over HTTP), one of which reports
  `opt_in_disabled` in practice, and the last of which only adds triplet
  embeddings;
* contradiction detection **flags and does not fix**, and is not retroactive.

None of that is a bug. Cognee is a per-document ingestion pipeline: ids are
deterministic so writes need no coordination, and the vocabulary is open so it
can be pointed at any corpus. Both choices buy ingestion throughput and pay for
it in graph quality. See §6 of the graph analysis.

The consequence is that a stale fact and its replacement are equally eligible
to be blended into one confident paragraph, with nothing in the output to say
so.

## 2. What cognee already gives us

**Structural lineage, and it is complete.** Every entity carries:

```
source_ref:v2:<dataset_id>:<data_id>:<chunk_id>        one per appearance
source_dataset_ids:  <dataset_id>
source_run_refs:     source_run_ref:v1:<pipeline_run_id>:<the source_ref>
```

Measured: the `ameer` node carries 35 source_refs -- one per record it appears
in. This means **any entity or assertion can be walked back to the exact chunk
and the exact ingestion run that produced it**, without guessing. That is the
single most useful thing cognee offers this design.

`provenance_edge_evidence` in Postgres carries the same idea at edge level and
covers the whole corpus (it predates enabling `PROVENANCE_TRACKING`).

**A candidate queue.** Measured: **40 `contradicts` edges** exist today. Small,
because detection is not retroactive and only covers writes since it was
enabled -- the ~1,432 imported records have none. A backfill pass would be
needed for the queue to cover the corpus.

Incidentally, `replaces` (4), `supersedes` (3) and `superseded_by` (1) also
exist as relationship names -- but those are LLM-extracted from record *text*,
not system-generated. The extractor reaches for supersession vocabulary on its
own.

## 3. What cognee does not give us

**Semantic origin.** There is no field anywhere that says a fact came from
Notion, or Claude Desktop, or a Discord thread. `source_dataset_ids` is the
closest thing and there is one dataset. Cognee's "source" answers *which write
produced this*, not *who told me this*.

The hooks for the latter already exist on `/api/v1/remember`: `node_set`
(a first-class graph citizen -- there are 2 NodeSet nodes today), `labels`,
and `external_metadata`.

Worth noticing: **the Mnemosyne header was a crude version of exactly this.**
`[mnemosyne] [working_memory | recorded 2026-09-03 20:50:10] [USER]` is source
+ tier + time. It poisoned the graph not because provenance is wrong but
because it lived in the *content* instead of the *metadata*, so the extractor
turned it into entities -- `mnemosyne` and `working_memory` are hubs #2 and #3.
The fix is to move provenance up a layer, not to delete it.

**Event time and validity.** Three different dates matter and cognee models
one:

| | what it is | cognee |
|---|---|---|
| record time | when it was written | `created_at` -- exists |
| event time | when the thing happened | only in prose |
| validity | when it started/stopped being true | nothing |

There is an alternate pipeline branch (`extract_events_and_timestamps` ->
`extract_knowledge_graph_from_events`, visible in `get_default_tasks`) aimed at
the second. It has not been investigated.

Validity is the one that consolidation actually needs, and nothing models it.

## 4. The idea

**Cognee is the substrate. Hermes is the curator.**

A scheduled Hermes job walks contradictions, reads the underlying sources --
its own conversation history, Notion, the repo -- decides what is true, and
records the decision.

### Why this decomposition is right, not a workaround

Consolidation is inherently **global and slow**: it needs many records in view
and real judgment per conflict. Ingestion is inherently **local and fast**: one
chunk, one LLM call, no coordination. These are opposite workloads. Cognee
chose the second and pays for it by never doing the first; bolting
consolidation into the write path would destroy the property that makes
ingestion cheap.

Separating them means **cognee generates candidates cheaply at write time and
Hermes adjudicates them expensively and rarely.** The `contradicts` edge is the
handoff.

### What it dissolves

A rule-based consolidator would need a declared winner rule per predicate --
single-valued vs multi-valued -- because "newer wins" is right for `works_at`
and wrong for `prefers`. That in turn would require a closed predicate
vocabulary, since you cannot annotate 2,273 freeform names.

An agent does not need that table. It reads both assertions and judges. The
cardinality problem disappears.

(The closed-vocabulary work in `scripts/cognee/extraction_prompt.txt` is still
worth doing for graph quality -- it is just no longer a precondition for
consolidation.)

### What it adds that cognee structurally cannot have

**External corroboration.** Cognee only knows what was written into it, so its
only possible notion of truth is "what the most recent record said." Hermes can
go look at the actual artifact. That is a different and much better epistemic
position, and it is the one place where owning the agent is a real structural
advantage over anything the vendor could ship.

Mnemosyne's `sleep` tool was reaching for the same idea. Converging on a design
twice from different directions is usually a good sign.

## 5. The crawl

From a `contradicts` pair, the inputs are:

1. **`source_refs`** -> the exact chunk behind each side, free and exact
2. **access to the originating artifact** -- the Notion page, the Discord
   thread, the conversation
3. **created / updated timestamps** on those artifacts
4. **a dispositive test**: does this directly settle the question?

### Assertion vs evidence

Both sides of a `contradicts` edge are assertions by construction. Neither is
self-validating. Evidence is something that makes one *more credible*, and the
two kinds behave differently:

* **Conversations are history.** They say what was true *when said*. Their
  value is mostly revision language -- "we switched", "that's wrong", "not
  anymore". Evidence about a **transition**.
* **Notion is current state.** A page asserts what is true *now*, with
  staleness risk. Evidence about a **value**.

So the adjudicator is not asking "which source outranks the other" but "can I
find a transition, or a current-state artifact, that settles this" -- two
different searches.

### The created/updated asymmetry

This is the sharpest available signal:

* a page **updated after** the newer assertion, still saying the old thing ->
  strong evidence the old thing survived a review;
* a page **created before and never touched** -> weak. Nobody has looked at it.
  **Absence of edit is not endorsement.**

That distinction -- *still true* vs *nobody has checked* -- is precisely what a
static source-precedence ranking cannot express, which is why precedence by
source type is the wrong model. Source type is a prior, not a verdict.

### Dispositive, not corroborating

Most things the crawl reaches will mention the topic without settling it. The
specific trap: **an assertion repeated in three places is still one
assertion.** A stale fact restated in a Notion page and a Discord message looks
well-supported to naive corroboration-by-count, when it is one mistake with
three echoes.

This is checkable rather than a judgment call: if every evidence path collapses
back to the same `source_ref`, there is one source wearing three hats. Cognee's
provenance makes that testable.

### Stopping rules

A crawl over a personal memory store will otherwise summarize your entire life
per contradiction. Natural terminators: found something dispositive; exhausted
the cheap sources; hit depth N.

## 6. Failure modes to design against

**It will always resolve.** An agent asked to settle contradictions produces
settlements, confidently, including when both sides are bare assertions with no
revision language and no corroborating artifact -- which will be common, given
how much of the corpus is offhand remarks. There must be an explicit and
*honorable* "cannot tell" outcome that leaves both in place, plus probably a
"needs you" bucket for conflicts only a human can settle. Without it you get
high-confidence coin flips laundered into ground truth.

**The closed loop.** An agent writing corrections into the store it later reads
as ground truth compounds its own errors: a bad consolidation becomes next
month's established fact, with the original evidence trail already collapsed.
This argues for consolidation being **additive and reversible** -- mark a
loser, do not delete it; keep the pre-consolidation state recoverable.

**Decisions must carry their evidence.** Stored with the outcome, not used and
discarded -- otherwise the next run redoes the search and may land differently.
The payload is *this won, because of this message on this date*, which is also
what makes a bad consolidation reviewable rather than archaeological.

**Retrieval has to surface it.** Cognee returns one synthesized paragraph and
"1 memory found". If a superseded flag is invisible in the output, a perfect
consolidator changes nothing that an agent consuming the answer can see. This
may be the more urgent gap than consolidation itself.

## 7. Open questions

**What does "make a correction" physically do?** The crux, and currently
unknown. Writing a corrected record just *adds* a third assertion to a two-way
conflict and grows the graph. Real correction needs either `forget` on the
source document -- unverified whether that removes derived nodes or orphans
them -- or direct graph mutation, which is outside the supported API. The whole
idea rests on this.

**Does the backlog get a queue?** 40 contradicts edges cover recent writes
only. Whether a backfill pass over ~1,432 imported records is feasible, and
what it costs, is unmeasured.

**Is the events pipeline the right home for event time?**
`extract_events_and_timestamps` has not been looked at.

**How do the three threads sequence?** Provenance-as-metadata, event time, and
consolidation are one design -- each is weak without the others -- but the
dependency order has not been worked out.

## 8. Related

* `docs/cognee-graph-analysis.md` -- measured state of the graph
* `claudedocs/research_cognee_graph_fragmentation_20260925.md` -- why cognee
  builds it that way, with source citations
* `scripts/cognee/extraction_prompt.txt` -- candidate extraction prompt
  (measured: date nodes 9 -> 0, import residue 10 -> 1, identity spellings 3 ->
  1, distinct predicates 51 -> 26 over a 24-record sample)
* `scripts/cognee/prompt_ab.py` -- the A/B harness that produced those numbers

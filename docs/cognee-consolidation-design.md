# Consolidation as a Hermes job: a design sketch

**Status: thinking, not a plan.** Nothing here is scheduled or agreed. It
exists so the reasoning survives the conversation it came out of.

Measurements are from the live `shared` dataset on 2026-09-25 and are marked
where they matter. Everything else is design reasoning and should be read as
provisional.

---

## 1. The problem this is trying to solve

Cognee accretes. Nothing in it consolidates, and nothing in it will
*automatically* -- but see §4: retraction at record granularity is supported
and correct, so the graph is repairable even though it never repairs itself.

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

## 2.1 Correction (2026-09-26): cognee already ships the sleep cycle

**Most of §5 describes a Hermes cron job that cognee 1.6.0 already implements
as a stage of `improve()`.** This was found by reading the running source; it
was not in the docs we had read. The rest of this document is still useful for
the *crawl* (§6) and the failure modes (§7), but the core "read sessions,
distill facts, write them back" loop should not be built from scratch.

`improve()` runs nine stages, in order:

```
feedback_weights, persist_session_qa, persist_agent_traces,
extract_agent_context, distill_sessions, update_user_preferences,
build_truth_subspace, triplet_enrichment, global_context_index
```

`cognee/modules/session_distillation/distill.py` describes its own flow:

> 1. LOAD    session QA turns + distillable session-context entries.
> 2. CURATE  pack the session timeline into batches; one curator LLM call per batch.
> 3. ACCEPT  per proposed lesson: search prior lessons/entities, then writer/rejecter LLM.
> 4. PERSIST render accepted lessons as documents; add + cognify them in one pass.

That is the design in §5, with two properties we had not specified: an
**accept/reject pass against prior lessons** (so a new lesson is checked
against what the graph already believes), and a **watermark**
(`session_persist_watermark`) that makes it resumable. Its failure semantics
are better than what we sketched:

> A failed curator batch or writer call drops only its own work mid-run, but
> the run then finishes by RAISING (after publishing what survived) instead of
> advancing the watermark: sealing entries behind calls that never ran would
> mark them distilled forever -- one LLM outage on a finished session would
> silently lose its lessons.

### It runs, and it produces nothing

Measured 2026-09-26, on every `improve()` call that afternoon:

```
improve: session Q&A persisted from 1 session(s)
improve: distilled session 'hermes_...' -> status=no_gated_entries documents=0
```

And in Postgres, `node_set` across all 802 records:

| node_set | records |
|---|---:|
| `["mnemosyne"]` | 778 |
| `["user_sessions_from_cache"]` | 16 |
| `null` | 8 |
| `["session_learnings"]` | **0** |

So the two session-fed stages have opposite outcomes. `persist_session_qa`
works -- it writes the raw `Session ID: ... Question: ... Answer:` documents,
which is where the transcript population comes from and why it regenerates
after deletion. `distill_sessions` returns `no_gated_entries` and zero
documents, every time. **The noisy half works and the useful half is inert.**

`no_gated_entries` means it found no *session context entries* clearing
`MIN_GATE_CONFIDENCE`. It reads those via `get_session_manager()`, not the
persisted Q&A documents -- confirmed in `distill.py:156-171` -- so the two
stages are independent, and disabling the dumper does not starve the distiller.
Why nothing clears the gate is the open question; `extract_agent_context` runs
immediately before `distill_sessions` in the registry and is the likely
producer of those entries.

### What follows

* **The transcript leak has a name and a switch.** It is
  `persist_session_qa`, disableable with `IMPROVE_STAGES_DISABLED`. That is a
  cognee-backend setting, so the dinefile profile (which runs against Cognee
  Cloud) is unaffected.
* **Do not build the consolidation cron yet.** Diagnose the gate first. A
  working `distill_sessions` makes most of §5 redundant.
* **Session entries are not time-expired.** No TTL, expiry, retention or
  max-age logic exists anywhere in `session_lifecycle/` or
  `session_distillation/`; invalidation is event-driven (e.g. on document
  delete). So a nightly job cannot arrive to find an empty cache, and a job
  that fails for days can catch up. This was the blocking unknown for the cron
  design and it resolves in the design's favour.

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

## 4. Correction is record-granular, and the graph is a projection

This was the sketch's lead open question. It is answered, and the answer
inverts one of the premises above.

**Cognee implements reference-counted graph retraction.** From
`infrastructure/databases/unified/provenance_delete_planner.py`:

> the planner decides which artifacts become *unowned* (no owning source ref
> remains -> hard delete) versus which merely *survive* (some ref remains ->
> detach the targeted refs only)

So `forget(data_id=..., dataset_id=...)` does not nuke or orphan anything. It
detaches that record's source refs from every node and edge it touched;
anything losing its **last** ref is hard-deleted along with its vectors;
anything still referenced survives with only the targeted refs removed.
Orphaned `EdgeType` nodes and NodeSet tags are pruned on the same pass, and
each of the three steps is individually idempotent and retry-safe.

Concretely: forgetting one record does not disturb `ameer` (35 refs). It does
cleanly remove the entities only that record mentioned.

### The correction primitive

No graph-mutation API is needed, and none is used:

```
forget(data_id)   surgical retraction of that record's contribution
remember(text)    re-derivation from the corrected text
```

**The record store is the truth; the graph is a projection over it.** And the
projection is rebuildable: `forget(dataset=..., memory_only=True)` drops graph
and vectors while *keeping the raw files*. Measured: 1,447 raw records,
155 MB, retained on disk today.

Three consequences:

* **The extraction prompt is not only for future writes.** The existing graph
  can be rebuilt under `scripts/cognee/extraction_prompt.txt` without
  re-seeding from Mnemosyne -- the records are already stored.
* **Consolidation has a conservative form.** The agent edits *records*; the
  graph follows. It never touches nodes.
* **"Cannot tell" is cheap.** Leaving a conflict alone costs nothing, because
  nothing structural was mutated to begin with.

Calibration: a full rebuild is ~1,433 records at ~9s ≈ **3.5 hours** of backend
time plus LLM spend. Expensive, not prohibitive; the shape of an overnight job
rather than something a human waits on.

### The new fault line: granularity

Correction is **record-granular**. Contradictions are **assertion-granular**.
That mismatch is now the interesting problem rather than the mutation API.

A record holding five facts, one of them wrong, can only be fixed by rewriting
all five. Re-extraction is non-deterministic, so the other four come back
*slightly different* -- a different predicate choice, possibly a different
entity split. Which means **repair is not idempotent**: running the
consolidator twice over the same conflict can leave two different graphs, and
convergence cannot be demonstrated.

The implication worth sitting with: **records should be small.** One assertion
per record makes correction surgical and makes non-determinism harmless,
because there is nothing else in the record to disturb. The Mnemosyne corpus is
already close to that shape by accident.

It also suggests the consolidator's real output may be **record surgery** --
split a compound record into atomic ones, then correct the single bad one --
rather than "pick a winner between two assertions."

## 5. The idea

> **Superseded in part -- see §2.1.** cognee 1.6.0's
> `distill_sessions` stage already implements this loop. Read that first;
> what remains live here is the crawl (§6) and the failure modes (§7).

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

### What access it needs: HTTP only

Everything the job needs is already on cognee's HTTP API, with the API key
Hermes holds. **No direct Kuzu access, no reaching into the container, no new
infrastructure.**

| need | surface |
|---|---|
| enumerate `contradicts` edges, read `source_refs` | `POST /api/v1/search`, `searchType: CYPHER` |
| whole-graph read | `GET /api/v1/datasets/{dataset_id}/graph` |
| the original record text behind a source ref | `GET /api/v1/datasets/{id}/data/{data_id}/raw` |
| retract / re-derive | `forget`, `remember` |

Cypher is the load-bearing one, and it is available here: the capability is
declared per adapter as `GraphDBInterface.supports_cypher_queries`, which
defaults to `True`; only the turso and postgres_demo adapters opt out. This
deployment runs Kuzu (Ladybug-backed), which inherits the default.
`SearchType.CYPHER` executes the query as given rather than generating it --
`SearchType.NATURAL_LANGUAGE` is the generating variant -- so the conflict
queue is a deterministic query, not an LLM guess.

One consequence for where the code lives: the Hermes *memory provider* surface
is `remember` / `recall` / `forget`, so it does not expose graph reads. The job
would talk to cognee's API directly rather than through the provider
abstraction. That is the right split anyway -- the provider is the agent's
memory, this is an administrative task against the store.

It also means the job is a pure client. It could run anywhere, and it does not
couple Hermes to cognee's storage layout.

### What it adds that cognee structurally cannot have

**External corroboration.** Cognee only knows what was written into it, so its
only possible notion of truth is "what the most recent record said." Hermes can
go look at the actual artifact. That is a different and much better epistemic
position, and it is the one place where owning the agent is a real structural
advantage over anything the vendor could ship.

Mnemosyne's `sleep` tool was reaching for the same idea. Converging on a design
twice from different directions is usually a good sign.

## 6. The crawl

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

## 7. Failure modes to design against

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

Note that §4 makes retraction *clean* but not *reversible*: once a record is
forgotten and re-added, the prior extraction is gone and cannot be diffed
against. If the pre-correction state is to be recoverable, the original record
text has to be archived outside cognee before the forget, because cognee keeps
no version history.

**Repair does not converge.** Re-extraction is non-deterministic and correction
is record-granular (§4), so a second pass over the same conflict can produce a
different graph than the first. Any consolidator needs a **termination
condition that does not depend on reaching a fixed point** -- a conflict marked
resolved stays resolved, rather than being re-derived and re-judged on the next
run.

**Decisions must carry their evidence.** Stored with the outcome, not used and
discarded -- otherwise the next run redoes the search and may land differently.
The payload is *this won, because of this message on this date*, which is also
what makes a bad consolidation reviewable rather than archaeological.

**Retrieval has to surface it.** Cognee returns one synthesized paragraph and
"1 memory found". If a superseded flag is invisible in the output, a perfect
consolidator changes nothing that an agent consuming the answer can see. This
may be the more urgent gap than consolidation itself.

## 8. Open questions

**Why does `distill_sessions` report `no_gated_entries`?** It is the
highest-value open question in this document: the sleep cycle exists and is
wired in, and nothing clears `MIN_GATE_CONFIDENCE` to feed it.
`extract_agent_context` runs immediately before it and is the likely
producer of session-context entries. Unresolved.

~~**What does "make a correction" physically do?**~~ **Answered in §4** --
reference-counted retraction via `forget`, then re-add. No graph mutation
needed, and shared entities are not collaterally damaged.

**Is a full rebuild worth doing, and does it converge?** §4 establishes it is
possible (~3.5h, raw records retained). Unmeasured: whether rebuilding under
the new extraction prompt actually improves the whole-corpus numbers the way it
improved the 24-record sample, and how much the result differs run to run.

**Should records be split before anything else?** If one assertion per record
is the right granularity (§4), that is a corpus-wide transformation that wants
doing *before* consolidation rather than after -- and it is not obvious whether
it is better done at re-seed time or as its own pass.

**Does the backlog get a queue?** 40 contradicts edges cover recent writes
only. Whether a backfill pass over ~1,432 imported records is feasible, and
what it costs, is unmeasured.

**Is the events pipeline the right home for event time?**
`extract_events_and_timestamps` has not been looked at.

**How do the three threads sequence?** Provenance-as-metadata, event time, and
consolidation are one design -- each is weak without the others -- but the
dependency order has not been worked out.

## 9. Related

* `docs/cognee-graph-analysis.md` -- measured state of the graph
* `claudedocs/research_cognee_graph_fragmentation_20260925.md` -- why cognee
  builds it that way, with source citations
* `scripts/cognee/extraction_prompt.txt` -- candidate extraction prompt
  (measured: date nodes 9 -> 0, import residue 10 -> 1, identity spellings 3 ->
  1, distinct predicates 51 -> 26 over a 24-record sample)
* `scripts/cognee/prompt_ab.py` -- the A/B harness that produced those numbers

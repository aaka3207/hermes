# The memory system: what it is for, and what it does today

Written 2026-09-26, at the end of a session that started as config hygiene and
turned into a redesign. It exists because the reasoning behind the design was
spread across a long conversation and would not survive it.

Everything in §4 is **measured**. Everything in §3 and §5 is **proposed** and
mostly unbuilt. The distinction is load-bearing: an earlier version of these
notes recorded a config switch as working fact, and it turned out to crash the
backend (`cognee-consolidation-design.md` §2.1). Claims here say which they are.

No personal record text appears in this repo. Where a real memory is used as an
example it is described by shape, not quoted.

---

## 1. The problem, stated properly

Memory had been treated as one thing. It is at least four, and they fail
separately:

1. **What is worth storing** — most of what passes through a session is not.
2. **Who stores it** — two agents write to the same places with no convention.
3. **Where the truth lives** when two stores disagree.
4. **Whether it comes back** when asked. This is the one nobody had measured.

The fourth turned out to be the binding constraint, and it had nothing to do
with the first three.

## 2. How the system is actually used

From an interview rather than inference.

**Two agents, one life.** Claude Desktop and Hermes are both daily personal
assistants, across several domains. Neither is the system of record.

**Notion holds the durable state** — changelogs, events, people, contacts,
lessons. Both agents write to it; the user does not write to it by hand any
more. Whether a write appends or replaces depends on the context, so no
global append-only rule is available.

**The recurring failures**, in the user's framing: it forgets; what it
remembers is stale or superseded; and getting a fact in takes too much
back-and-forth. The sharpest one is specific — Hermes *collides* something
stated on a Notion page on one date with something said to it directly on
another, and has no way to tell which is current.

**The conflict rule is decided**: most recent wins, and the agent says so.

**Cognee's job**, in the user's words, is "a recall engine, for preferences and
just things that have happened." Not an index of Notion. Its own thing. Notion
is simply the area that generates the most contention, which is what forced the
rest of this design.

**Not everything earns a Notion page.** Many preferences never justify one.
Those live in cognee alone, and that is fine.

**The cron matters more than in-session repair.** Maintaining memory during a
working session spends the context the session needs. Consolidation belongs in
a scheduled job.

## 3. The division of labour (proposed)

**Cognee is history. Notion is current state.**

* Cognee records are **append-only dated assertions**. They are never edited to
  stay true. A record says what was the case on a date, and remains correct as
  a statement about that date forever.
* Notion pages are **mutable and current**. A page is the live answer.
* When an agent creates a Notion page, it writes one cognee record saying the
  page was created, when, and what it covers. **Page edits produce no further
  cognee writes.** The page carries its own present tense.
* Either agent may write to cognee. Whichever one acts, writes.

This is what dissolves the collision problem. A cognee hit that mentions a
Notion page is not a competing fact — it is a **pointer**, and the instruction
it carries is *go read the page*. The page wins, because the page is the only
one of the two claiming to be current.

The cron then does the consolidation: look at what happened, recall against it,
and where a loose fact plainly belongs on an existing page, put it there.
Where it does not, leave it in cognee to surface on its own.

**Status: unbuilt.** No agent follows this convention yet. The one record
written under it in testing was written in ordinary prose, with the page id
inline as free text rather than in any parseable form — and retrieved fine
anyway (§4.3).

## 4. What was measured on 2026-09-26

### 4.1 Both agents write to the same store

Claude Desktop's writes land on the self-hosted backend, in `shared`, not on
Cognee Cloud. Five records carry its `[claude-desktop]` prefix, the earliest
from 2026-09-23. This had been assumed the other way round; the dataset listing
showed no Claude-specific dataset, and the wrong conclusion was drawn from it.

The shared store that §3 depends on already exists.

### 4.2 Records survive recall intact

A ~940-character record came back whole, with an embedded Notion page id
readable in the returned text. The `_normalize_recall_item` whitelist in the
provider passes six fields and drops the rest, but `text` is not truncated.
A previously suspected 500-character cap does not exist on this path.

### 4.3 The real failure is the search type, and `auto_route` picks it wrong

The same query, two ways:

| query `<name> page Notion` | result |
|---|---|
| through Hermes, `auto_route: true` | *"Could you please resend the full Notion page URL?"* |
| direct, `search_type: CHUNKS` | **the right record, ranked first** |

The data was never the problem. `auto_route` classified a three-word query as a
question and ran a **completion** — an LLM writing prose over the graph —
instead of a retrieval.

### 4.4 Why the completions are bad

The completion model **imitates the corpus instead of querying it**. The graph
is full of conversational turns in which the assistant asks the user for a
link, so asked about a page, it produces another request for a link.

`SessionQAVector_text` holds **246 raw `User:`/`Assistant:` pairs**, 27 of them
under 200 characters — `User: Test / Assistant: Test received.` and the like.
These come from `persist_session_qa`, the `improve()` stage that cannot be
disabled without crash-looping the backend. It is supplying the completion
model with a script to copy.

The string that first exposed this, returned by recall as if it were a memory,
was not stored anywhere. It was generated.

### 4.5 `auto_route: false` is a trap

`http_backend.py:576`: with `auto_route` off and no explicit type, the request
is sent as `GRAPH_COMPLETION` — the **worst** performer measured. Turning the
config switch off makes recall worse, not better. There is no config key for a
default search type; `search_type` exists only as a per-call tool argument.

### 4.6 Two smaller findings

* **`score` is a distance, not a similarity.** The correct hit scored 0.466,
  the lowest of five returned, and ranked first; results sort ascending. Any
  consumer filtering `score > threshold` inverts the ranking.
* **One-word queries fail under every search type.** The practical floor is
  about three words. A bare proper noun — the most natural thing an agent would
  ask — returns nothing usable.

### 4.7 The corpus now contains its own recall output

The transcript of the session that ran these tests was persisted by
`persist_session_qa`, including the pasted JSON of the recall results. The junk
string that recall returned is now stored text that a future recall can return
for real. The loop closes without anyone deciding it should.

## 5. Decisions taken, not yet executed

1. **Rebuild the cognee store from empty.** The corpus is too contaminated to
   evaluate against — 800 records of which a large share are transcript
   fragments and conversational turns, now including this session's own output.
   §4 is only interpretable against a clean store.
2. **`improve_on_end: false`** on the personal profile. It is the only switch
   that stops the session-transcript write path, since `persist_session_qa`
   cannot be disabled individually.
3. **Pass `search_type: "CHUNKS"` on recall.** The single highest-value change,
   and it is an instruction to the agent rather than a config edit, because no
   config key for it exists (§4.5).

Sequencing matters: 2 before 1, or the wipe refills from the next session end.
Runbook in `cognee-operations.md` §9, "Wiping the store and starting clean".

**No re-seed.** Decided 2026-09-26: the 517 records the triage kept are not
worth reinstating either. Both populations are backed up to JSON first — the
documents, and the `SessionQAVector_text` session turns, which the older
document backup never captured.

**Not yet settled** — whether a short tagged pointer record retrieves better
than the prose one that worked in §4.3. Eleven seed records are staged and
uncommitted against that question. Until it is answered, nothing justifies
imposing a tag format on either agent.

## 6. Open questions

* Does the three-word floor (§4.6) hold on a clean store, or is it an artifact
  of the conversational noise? This decides whether the corpus wipe fixes
  retrieval or merely tidies it.
* With `improve_on_end: false`, which of `improve()`'s other eight stages are
  lost, and does any of them matter? `distill_sessions` already produces
  nothing (`cognee-consolidation-design.md` §2.1), so the cost may be zero.
* Who runs the consolidation cron, and against what — Notion's
  `last_edited_time` ordering is available and was verified live, contrary to an
  earlier claim that no workspace-wide modified-since query existed.
* `SOUL.md` still instructs a Mnemosyne write on Notion resource creation. That
  path is dead and the instruction needs replacing with the §3 convention.

## 7. Related

* `cognee-operations.md` — architecture, config resolution, the `api_key` trap,
  the runbook for retiring the container-wide `COGNEE_*` variables
* `cognee-corpus-shape.md` — what was in the imported corpus and what the
  mechanical cut removed
* `cognee-graph-analysis.md` — why the graph built from that corpus is a poor
  query surface
* `cognee-consolidation-design.md` — retraction semantics, and the `improve()`
  stages including the fatal one

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
2. **`improve_on_end: false`** on the personal profile, since
   `persist_session_qa` cannot be disabled individually. **It stops transcripts
   reaching the permanent dataset only — per-turn writes into the session cache
   continue and cannot be switched off. See §5.3.**
3. **Pass `search_type: "CHUNKS"` on recall.** The single highest-value change,
   and it is an instruction to the agent rather than a config edit, because no
   config key for it exists (§4.5). §5.2 raises the stakes on this one: on a
   clean store the completion modes still corrupt names and dates, so CHUNKS is
   the only mode safe for anything an agent will act on.

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

## 5.1 Executed 2026-09-26, and what the empty store showed

`improve_on_end: false`, then backup, then wipe, in that order. 834 records and
247 session turns backed up to JSON on the host; `shared` deleted outright;
the graph emptied (0 nodes, 0 edges — the Kuzu file stays 3.5 MB because Kuzu
does not shrink on delete).

**`/api/v1/forget` did not clear `SessionQAVector_text`.** The suspicion in the
runbook was right: the endpoint is dataset-scoped, that table is session-keyed,
and 247 rows survived a wipe that removed everything else. They were truncated
explicitly, along with `session_records` and orphaned provenance. Anyone
repeating this must check — a wipe that leaves them behind leaves the exact
population that causes §4.4.

**The query-length floor was entirely corpus noise.** One synthetic record was
seeded into the empty store and queried at one, two and four words:

| query | CHUNKS | GRAPH_COMPLETION |
|---|---|---|
| one word | the record | a correct summary of it |
| two words | the record | a correct summary |
| four words | the record | a correct, formatted answer |

Against the old corpus the one-word case returned conversational junk under
every search type (§4.6). It now works. **This confirms §4.4 by removal**: the
completion mode was not broken, it was imitating the transcript turns, and with
them gone it answers properly. §4.6's floor should be read as a symptom of the
old corpus, not a property of cognee.

A write into the empty store took **4.1s**, against the ~9-11s measured on the
bloated one.

The seeded record was deleted afterwards. The store is empty.

## 5.2 The real pointer record, on the clean store

Claude Desktop re-wrote the same Notion-pointer memory into the empty store
(one record, ~940 chars, prose, page id inline as free text). The queries that
failed in §4.3 were re-run against it.

**Every query returns the record under every search type.** Including the bare
one-word name, and including a natural-language query nobody tuned for —
*"what Notion pages do I have about dating"* returns the page with its id. That
is the §3 pointer behaviour working end to end, with no tag format and no
convention: ordinary prose was enough.

**But the completions fabricate, and the graph proves it.** In several
completion answers a person's name came back **truncated** — the last two
characters dropped — and one answer put the record's creation date a day late.
The stored graph is correct: the extracted entities carry the full name and the
right date, so nothing is corrupted at rest. The completion LLM is mangling it
at answer time.

This upgrades the §5 recommendation. `search_type: "CHUNKS"` is not merely
better-ranked: it is the **only mode that returns facts unaltered**. Every
completion mode paraphrases, and paraphrase of an identifier is corruption — an
agent acting on a truncated name searches Notion for a page that does not
exist. A clean corpus fixed retrieval; it did not make completions safe for
identifiers.

Unchanged from `cognee-graph-analysis.md`: bare dates are still extracted as
standalone entities (six of them from this one record).

## 5.3 Correction: `improve_on_end: false` does not stop transcript writes

Recorded because it was stated as fact in this document's first draft and in
the session that produced it, and it is wrong.

There are **two** write paths, and the flag only closes one:

| path | what it writes | gated by |
|---|---|---|
| `improve()` → `persist_session_qa` | transcripts into the **permanent** dataset | `improve_on_end` |
| `sync_turn` → `/api/v1/remember/entry` | every turn into the **session cache** | nothing configurable |

`sync_turn` (`provider.py:565`) fires per turn, in a background thread, and
checks only `_writes_enabled` — which is set from `agent_context`
(`provider.py:316`), i.e. whether this is the primary agent rather than a
subagent. **There is no user-facing switch for it.**

Measured: `SessionQAVector_text` and `session_records` were truncated to zero,
and a six-turn Hermes conversation refilled them to 6 and 4 within five
minutes, with `improve_on_end: false` in force the whole time.

**This is why recall returns the agent's own previous answers.** `scope`
defaults to `auto`, and `_recall_scope_params` maps `auto` onto *both* the
session cache and the permanent dataset (`provider.py:876`). In the observed
conversation the assistant answered a question about a person at 19:58; at
20:03 the same assistant was asked who that person was, recalled its own 19:58
answer out of the session cache, and reported it as a stored memory. The loop
from §4.7 is not a slow corpus-contamination problem — it closes **within a
single session, in minutes**.

**So the recall instruction needs two arguments, not one:**

```
scope: "graph"          # permanent dataset only, no session cache
search_type: "CHUNKS"   # verbatim records, no completion paraphrase
```

Both are per-call tool arguments (`schemas.py` `RECALL_SCHEMA`). Neither has a
config default, so neither survives an agent that forgets to pass them.

## 5.4 Order of work

Three stages, in this order, because each depends on the one before:

1. **Get the store right.** Done as of 2026-09-26 — wiped, one real record,
   retrieval verified. The limits in §5.2 and §5.3 are known and worked around
   rather than fixed.
2. **Update the prompts**, for both Hermes and Claude Desktop: the §3 write
   convention, and `scope: "graph"` + `search_type: "CHUNKS"` on every recall.
   Nothing downstream works until both agents behave consistently.
3. **Build the consolidation cron.** Last, because it acts on records written
   under the convention in stage 2 and there is no point running it over
   records that predate it.

**The provider decision is still open.** After the wipe, a direct query
returned the stored record cleanly at every query length — but the agent's
*answer* in normal use was still wrong, because it came from the session cache
and a second memory system rather than the store (§5.3). That is a fair reason
to doubt the whole arrangement: a memory system whose correct retrieval is
routinely bypassed by an uncontrollable cache is not obviously worth keeping.
The three criteria that selected cognee — custom model and embedding
endpoints, shareable with Claude Desktop, capable of running a life — are
unchanged, and it still meets them. What is now also known is the cost:
two mandatory per-call arguments with no config default, and a write path with
no off switch. Revisit with that on the table, not just the criteria.

## 6. Open questions
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

## 9. Three tiers, not two

Added 2026-09-26, after the store was rebuilt. §3 described a two-way split
between memory and Notion. That was one tier short, and the missing one is why
a whole population of records was undecidable during triage.

| tier | store | holds |
|---|---|---|
| 1 | Hermes' `/opt/data/memories/MEMORY.md`, `USER.md` | preferences about **how this agent should behave** |
| 2 | the shared memory store | preferences and events **about the user**, that every agent needs |
| 3 | Notion | durable life state — the global shared store |

The test for tier 1 versus tier 2 is *who the fact is about*. "Deploys via
Coolify, so check the injection layer before diagnosing credentials" is about
Hermes. "Prefers double progression when programming lifts" is about the user,
and Claude Desktop needs it just as much.

**This resolves the triage's hardest question retroactively.** 31 of the 74
records that three classifier passes could not settle were operational agent
text — worker prompts, cron preambles, delegation notices
(`cognee-corpus-shape.md` §6). The classifier wavered because *"is a reusable
instruction a memory?"* has no answer without this distinction. With it, the
answer is immediate: it is a memory, in tier 1, and it was in tier 2 because
tier 1 had no stated boundary.

**Hermes' `MEMORY.md` currently mixes all three.** 22 entries holding agent
deployment facts, user preferences that belong in the shared store, Notion
pointers with URLs, and stale entries describing a retired provider. Sorting it
is a prerequisite for stage 2 of §5.4, not a follow-up: the prompts cannot say
where a fact goes until the tiers are separated in the one place that already
has facts in it.

**Note the pointer entries.** Several `MEMORY.md` records are already
"Created Notion resource: <title> — <url>". That is precisely the §3 pointer
pattern, invented independently and filed in the wrong tier. The convention
does not need inventing; it needs moving and making consistent.

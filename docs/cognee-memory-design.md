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

## 0. Start here

The sections below were written in order during one long session, so they read
as a narrative. This is the state they arrived at. **§9-13 supersede §3 where
they disagree** -- §3 describes two tiers and the real answer is four.

### The architecture

| tier | store | holds | both agents? |
|---|---|---|---|
| 1 | `memories/MEMORY.md`, `USER.md` | how *this agent* should behave | Hermes only |
| 2 | `hermes-lcm` (`/opt/data/lcm.db`) | everything ever said, losslessly | Hermes only |
| 3 | shared store (cognee today) | user facts every agent needs | both |
| 4 | Notion | durable current state | both |

Tier 3 records are **dated assertions that are never edited**, carrying up to
two pointers: a Notion page id (what is true now) and a Hermes session id (what
was actually said). Tier 4 pages are mutable and win on current state. §11 has
the read/write routing; §12 the pointers; §13 attribution.

### Done on 2026-09-26

* Store wiped to empty and backed up (834 records + 247 session turns + 33
  session records, as JSON on the host). No re-seed.
* `improve_on_end: false` on the personal profile; gateway restarted.
* `TELEMETRY_DISABLED=1` on the cognee service.
* Retrieval verified working from empty at one, two and four words.

### Done on 2026-09-27

* The plugin now installs from a fork, which turns the two recall levers into
  config keys and gives the per-turn session write a switch. Two of the four
  problems were already fixed upstream. **§14** has the detail; it supersedes
  §4.5 and §5.3 where they say no config default exists.
* The prompt layer: tier 1 sorted, `SOUL.md` carrying the §11 routing, and the
  dead Mnemosyne and GBrain references removed. **§15** has the detail --
  including the four injected memory layers, two of which were undocumented, and
  the ~4,500-token block that arrives on every prompt regardless of the prompt.

### Done on 2026-09-29

* Backend on cognee 1.6.1, which copies a document's metadata onto its chunks.
* Every permanent Hermes write now carries dated, attributed metadata, and
  `CHUNKS` recall returns it. Hermes passes Notion pointers as
  `metadata.notion_page_id`. `SOUL.md` explains the search modes, so Hermes
  can choose between exact text and graph synthesis itself. **§16** has the
  detail.

### What is left, in order

Steps 1 and 2 below are **done** -- see §15, which supersedes them.

1. ~~Sort `MEMORY.md`.~~ Done 2026-09-27: 12 entries to 2. `USER.md` was
   deliberately *not* sorted; it is tier 1 doing its job, and only two entries
   were wrong (§15).
2. ~~Update both prompts with the §11 routing.~~ Done for Hermes `SOUL.md`.
   **Claude Desktop's prompt is still not updated.**
3. **Deal with the auto-injected block before the cron.** §15: roughly 4,500
   tokens land in every prompt, a third of it prior LLM answers, and one
   measured block contradicted itself. The block's shape is upstream design
   (§15, "Correction, 2026-09-29"); what is wrong is the history it carries, and
   where that history comes from is not yet established. Find the source first.
   A consolidation cron has nothing reliable to act on while that is true --
   the same reason step 3 used to wait on step 2.
4. **Then** build the consolidation cron.
5. **Decide what crosses the tier-3 / tier-4 boundary** (§15 "Open"). Tier 3 is
   specified as pointers, but descriptions of Notion content are being stored
   too, and they go stale against a mutable tier 4.

### Open decisions

* **Whether to keep cognee at all.** Its graph was measured near-useless and
  retrieval rides on embeddings plus the LLM
  (`cognee-graph-analysis.md` §6). Since `search_type: CHUNKS` became the
  default, explicit recall does not touch the graph at all (§16). Every write
  still pays the LLM extraction that builds it, and the injected block is its
  main reader. Candidates researched 2026-09-26 in
  `claudedocs/research_honcho_20260926.md` and
  `claudedocs/research_openviking_20260926.md`; OpenViking was re-checked
  against v0.4.23 on 2026-10-04 in `openviking-analysis.md` (backups fixed,
  extraction risk overstated, still untested). **Cheapest experiment first:**
  `LCM_ASSERTIONS_ENABLED` and `LCM_EMBEDDINGS_ENABLED` are two environment
  variables on a plugin already installed and already serving every session
  (§10).
* **Claude's Notion authorship is not recoverable** from the API -- it writes
  as the user, not as a bot. Needs its own integration token or an in-page
  convention (§13).

### Why every provider looks broken in the same way

Four memory providers were examined closely (cognee in use, Mnemosyne retired,
Honcho and OpenViking researched 2026-09-26). Every serious defect found in any
of them sits in the **LLM extraction layer**, and none sits in storage:

| provider | where it fails |
|---|---|
| cognee | cognify builds an unusable graph; completions corrupt names and dates |
| Honcho | the deriver writes its own prompt's examples into facts about the user |
| OpenViking | extraction drops, merges and deletes records (2026-09-26; overstated for plain `write`s, see `openviking-analysis.md` §4) |
| Mnemosyne | the consolidation cycle produced nothing, then froze |

The inverse also holds. Each one's plain read path works: cognee's `CHUNKS`,
Honcho's verbatim row search with a literal-substring branch for identifiers,
OpenViking's `grep`/`read`, LCM's FTS across 21,335 messages with no vectors
configured at all.

So the category is not broken; it is **competing on the layer that does not
work**. Autonomous memory extraction demos well and is unreliable everywhere;
store-index-return-exactly is reliable everywhere and is nobody's headline.

**Two consequences for this design.** First, provider choice matters less than
it appears -- if the answer at every provider is *disable the LLM layer and use
it as a store*, they are near-interchangeable, and the real criterion becomes
whose failures are loud. OpenViking failed that test on 2026-09-26 (config flags
declared and never read; `ov snapshot commit` broken since 0.4.19 with nightly
backups failing unnoticed). `ov snapshot` was fixed by 2026-09-29, and the
`recallExcludeUris` flag is wired in v0.4.23, so re-check
`openviking-analysis.md` §2 before relying on this line. LCM passes it by being
already installed, already holding everything, and visibly in the request path.

Second, the convention in this document -- deliberate writes, dated assertions,
explicit pointers, verbatim reads, a cron that consolidates -- is doing by hand
what these products automate badly. On the evidence that is not a workaround;
it is the correct architecture.

### Traps that already cost time

* `auto_route: false` makes recall **worse** -- it hardcodes `GRAPH_COMPLETION`.
* Completion modes corrupt names and dates even on a clean store. Anything
  acted on must come back verbatim.
* `score` is a **distance**; a `> threshold` filter inverts the ranking.
* `/api/v1/forget` does not clear `SessionQAVector_text`.
* `improve_on_end: false` does **not** stop per-turn session writes.

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

> **Superseded 2026-09-27 (§14).** The last sentence no longer holds: the
> forked plugin adds a `search_type` config key. The first three sentences
> still do — `auto_route: false` remains a trap, and the fork deliberately did
> not change that behaviour, only named the constant behind it.

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
   the only mode safe for anything an agent will act on. **As of 2026-09-27 this
   is a config edit after all — see §14.** The reasoning stands; only the
   delivery mechanism changed, and for the better: a config default cannot be
   forgotten by a caller.

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

> **Both halves of that are fixed as of 2026-09-27 (§14).** `scope` is gone —
> upstream deleted `_recall_scope_params`, and recall now targets the graph
> unconditionally. `search_type` has a config default in the forked plugin. The
> measurement above stands; what changed is that it no longer depends on an
> agent remembering anything.

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

## 10. The fourth tier: hermes-lcm, and what is already installed

Investigated 2026-09-26 after the tier model (§9) turned out to be missing a
layer. The correct stack is **four** tiers:

| tier | store | holds |
|---|---|---|
| 1 | `memories/MEMORY.md`, `USER.md` | how this agent should behave |
| 2 | `hermes-lcm` (`/opt/data/lcm.db`) | everything ever said, losslessly |
| 3 | the shared memory store | user facts every agent needs |
| 4 | Notion | durable life state |

### What LCM is

**Lossless Context Management** — `config.yaml` sets `context.engine: lcm`, so
it is the active *context engine*, not an add-on. It persists every message to
SQLite with FTS, compacts older turns into a summary DAG, and exposes recall
tools that drill back to exact text. Upstream: `stephenschoettler/hermes-lcm`,
MIT, based on the LCM paper (Ehrlich & Blackman, Voltropy, Feb 2026).

Live contents: **21,335 messages across 132 sessions**, 73 summary nodes,
112 MB. This is why the agent could answer a question about a person with no
matching record in the shared store — it searched its own conversation history.
Attributing that answer to the shared store, as this document's §5.3 example
initially did, was wrong about the source even though it was right about the
mechanism.

### It already implements most of the design in §3

Two documented capabilities line up almost exactly with what §3 proposes:

* **`lcm_recall`** — searches all conversations by meaning, fusing full-text
  with summary-vector and chunk-vector arms via RRF, and returns **bounded
  verbatim excerpts** with expand handles. Verbatim retrieval and lexical
  matching are the two things the shared provider was measured to fail at
  (§4.6, §5.2). Degrades to FTS-only when embeddings are off.
* **`lcm_query_state`** — a same-DB assertion sidecar returning typed state
  (facts, preferences, commitments, status) by subject, with an **as-of
  boundary**, and every result carrying an exact message id, character span,
  hash and quote. That is the dated-assertion model in §3, already specified.

One deliberate difference worth noting: `lcm_query_state` "preserves unresolved
conflicts and never treats recency alone as supersession." The stated rule in
§2 is *most recent wins, and say so*. These are compatible — saying so requires
seeing both — but the resolution has to happen in the prompt, not the store.

### Both are switched off

Measured from the live database and environment:

| flag | default | here |
|---|---|---|
| `LCM_ASSERTIONS_ENABLED` | `false` | **not set** — no assertion tables exist in `lcm.db` |
| `LCM_ASSERTION_EXTRACTION_ENABLED` | `false` | **not set** |
| `LCM_EMBEDDINGS_ENABLED` | `false` | **not set** — no vector tables; `lcm_recall` is running FTS-only |

Only `LCM_HERMES_BASE_DIR` is set. So LCM is running as lossless storage plus
lexical search, with its semantic retrieval and its entire assertion layer
dormant.

`LCM_EMBEDDING_PROVIDER`, `LCM_EMBEDDING_MODEL` and `LCM_OLLAMA_BASE_URL`
exist, so the custom-endpoint requirement that drove the original provider
choice is satisfied here too.

### What this means for the provider question

The capability being built in tier 3 substantially exists in tier 2, unenabled,
in a plugin already installed and already serving every session. Before
replacing the shared store with a different product, the cheaper experiment is
to turn on what is already here and measure it — `LCM_ASSERTIONS_ENABLED` and
`LCM_EMBEDDINGS_ENABLED` are two environment variables.

**Unverified, and the reason this is not yet a recommendation:** enabling the
sidecar "alone performs no extraction or backfill" (operator-guide.md:259), so
what it costs to populate against 21,335 existing messages is unknown, as is
whether extraction quality is any better than the graph extraction that failed
in tier 3. Measure before committing.

## 11. Routing: when to reach for which layer

This is the content of stage 2 in §5.4. It has to be answerable in one line per
case, or an agent will not follow it.

### The asymmetry that drives everything

| layer | Hermes | Claude Desktop |
|---|---|---|
| 1 `MEMORY.md` / `USER.md` | yes, always in context | **no** |
| 2 `hermes-lcm` | yes | **no** |
| 3 shared store | yes | yes |
| 4 Notion | yes | yes |

Two of the four layers are Hermes-only. **So anything the other agent will ever
need must live in tier 3 or tier 4.** A fact Hermes files in `MEMORY.md` or
leaves sitting in its transcript is, from Claude Desktop's side, a fact that
does not exist. This is the single rule most likely to be violated in practice,
because from inside a Hermes session all four layers feel equally available.

### Read routing

| the question is… | go to | why |
|---|---|---|
| how should I behave, what does the user want from *me* | **1** | already in context; no retrieval call |
| what did we actually say, decide, or agree — and when | **2** LCM | it has the transcript verbatim, with dates and exact spans |
| what is true about the user, stated once, needed everywhere | **3** | the only durable layer both agents share |
| what is the current state of a project, person, plan, or account | **4** Notion | the only layer that claims to be current |

The 2-versus-3 line is **who said it and where**, not how important it is.
Conversation provenance is tier 2. A standing fact about the user is tier 3,
even when it was first said in a conversation — in which case it should have
been *written* to tier 3 at the time (see below).

### Write routing

When something new is learned, ask **who the fact is about**:

* about **this agent's behaviour** → tier 1.
* about **the user**, and small — a preference, a one-off fact that does not
  justify a page → tier 3. Write it at the moment it is learned, not later.
* about **the user**, and substantial — an event, a person, a decision with
  ongoing state → tier 4, as a page, **plus one tier-3 pointer record** saying
  the page exists, when it was created, and what it covers, including the page
  id. Edits to that page produce no further tier-3 writes.
* nothing is ever *written* to tier 2. It captures itself.

### Four rules that cut across all of it

1. **Anything you will act on must come back verbatim.** Ids, dates, amounts,
   titles. Use tier 2's exact refs or tier 3 with `search_type: "CHUNKS"`.
   Never a completion — completions paraphrase, and a paraphrased identifier is
   a wrong identifier (§5.2).
2. **A tier-3 hit naming a Notion page is a pointer, not an answer.** Fetch the
   page before answering from it. The page is current; the record only claims
   the page exists.
3. **On conflict, most recent wins — and say so, with where each came from.**
   Tier 4 beats tier 3 on current state, because tier 3 records are dated
   assertions and never claim to be current.
4. **Never answer from tier 2 alone for something the other agent should also
   know.** If the answer came out of a transcript and it is durable, write it
   to tier 3 in the same turn. Otherwise it stays invisible to Claude Desktop
   and dies with the conversation.

### Where recall actually goes wrong today

The observed failure in §5.3 was a routing failure, not a storage failure: the
agent answered a tier-3 question ("who is this person") out of tier 2, then
reported it as a stored memory. Both halves were wrong — the wrong layer, and a
provenance claim it had not checked. Rule 4 and rule 1 exist for exactly that.

## 12. Tier 3 points in two directions

Tier 3 is not only an index of Notion pages. A record can also carry the
**Hermes session id** of the conversation where the fact was stated, which
makes tier 2 dereferenceable from tier 3.

| pointer on a tier-3 record | resolves to | answers |
|---|---|---|
| Notion page id | tier 4 | what is true *now* |
| Hermes session id | tier 2 | what was *actually said*, verbatim, and when |

Together they give a record both a current-state destination and a provenance
destination, and neither has to be duplicated into the record itself.

**SOUL.md already requires this.** Its standing instruction is to obtain and
confirm the originating session id through the LCM tool before storing a
memory, to include that verified id, and — explicitly — never to infer or
invent one: if LCM cannot confirm it, the memory must not be stored as though
provenance were verified. The convention predates this design; what it lacked
was a stated reason and a verified resolution path.

### The join is real (verified 2026-09-26)

Three spellings are in play, and they do reconcile:

* `lcm.db.messages.session_id` holds the bare Hermes id, e.g.
  `20260926_152622_5bfbb3`. 130 of 132 sessions use this shape; 2 do not.
* the cognee session id is `hermes_` + that same string
  (`_build_cognee_session_id`).
* stripping the prefix and querying `messages` returns the real rows — tested
  against a live session, 32 messages found.

So the chain **tier-3 record → session id → `lcm_load_session` → exact raw
transcript, paginated** works today with no new plumbing. `lcm_expand` with a
`store_id` narrows it to a single message, and `include_exact_ref=true` yields
a citable span.

### What this is good for

It resolves the conflict case properly. When a tier-3 assertion disagrees with
a Notion page, the agent can do more than compare dates: it can open the
conversation the assertion came from and read what was actually said, in
context, before deciding. That is the difference between *most recent wins*
and *most recent wins, and say so* — §2's rule, with the evidence to back it.

### The limit worth stating

**Only Hermes can dereference a session id.** Claude Desktop has no LCM tools,
so for it the id is an opaque token — evidence that provenance exists, not
access to it. The rule in §11 stands: anything Claude must actually be able to
read belongs in the record text or on a Notion page, not behind a session
pointer.

## 13. Attribution: who wrote this

The third axis, alongside the two pointer directions in §12. Measured
2026-09-26; it is available at every tier, but by three different mechanisms
and with one real gap.

| tier | mechanism | status |
|---|---|---|
| 2 LCM | `messages.source` column | **free, but it names the surface, not the agent** |
| 3 shared store | ledger columns are NULL → text prefix | **convention only, and only one agent follows it** |
| 4 Notion | `created_by` / `last_edited_by` | **free for Hermes; Claude is invisible** |

### Tier 2 — populated, but answering a different question

`source` is set on all 21,335 messages and is genuinely useful:

```
desktop 13068 · discord 3990 · photon 1737 · tui 1003
subagent 770 · cron 574 · curator 149 · cli 34 · api_server 10
```

But these are **Hermes channels**, not agents — `desktop` is the Hermes
desktop UI, not Claude Desktop. It distinguishes *interactive from cron from
subagent*, which matters (it is how the operational-text problem in §9 could be
caught automatically), but it never says "Claude wrote this", because Claude
does not write here at all.

### Tier 3 — nothing native, and a half-adopted convention

Cognee's ledger columns (`source_user`, `source_pipeline`, `source_task`,
`source_node_set`, `ontology_uri`, `valid_to`) were NULL on 30 of 30 sampled
records. Attribution has to live in the record text.

In practice one agent already does this: Claude Desktop prefixes
`[claude-desktop]`, and the retired importer prefixed `[mnemosyne]`. **Hermes
prefixes nothing.** So today an untagged record means "probably Hermes, or
possibly anything" — which is not attribution. Making Hermes tag its writes is
a one-line prompt change and closes this.

**Closed for Hermes, 2026-09-29 (§16):** its writes now carry
`created_by: hermes` as metadata, not as a text convention. Claude Desktop is
still text-prefix only, because `cognee-mcp`'s `remember` takes no metadata.

### Tier 4 — the gap

Across the 100 most recently edited pages there are exactly **two** actors:

* `Hermes` — type `bot`, 81 created / 85 last-edited
* `Ameer Akashe` — type `person`, 19 created / 15 last-edited

**Claude Desktop does not appear.** Its Notion connector authenticates as the
user over OAuth rather than as its own integration, so its writes are recorded
under the person account. Given the user does not hand-edit Notion, most of
those 19 pages are Claude's — but nothing in the API distinguishes a page
Claude created from one the user created.

So "Claude wrote this page" is **not recoverable from Notion metadata**, and
any design that assumes it is will be quietly wrong. Two ways out, neither
free: give Claude its own Notion integration token instead of the OAuth
connector, or have Claude stamp authorship into the page itself (a property or
a first line) as a convention — the tier-3 approach, applied one tier up.

### Why this matters for conflict resolution

§2's rule is *most recent wins, and say so*. Attribution makes "say so" mean
something: **"Claude wrote this on the 7th; you told me otherwise on the 19th"**
is a resolvable statement, where two undated unattributed claims are not.
Combined with §12's session pointer, the agent can name the writer, the date,
and the conversation. That is the full answer to the original complaint —
colliding Notion state with things said directly and not knowing which is true.

It works at three of the four corners today. The missing corner is Claude's
Notion authorship, and it needs a convention rather than a query.

---

## 14. What the plugin fork changed, 2026-09-27

§4 diagnosed the failure as a *search-type* problem rather than a data problem,
and §5 could only answer it by instructing the agent, because the levers were
per-call tool arguments with no config defaults. That was the last unresolved
piece: an instruction an agent can forget is not a fix, and the per-turn write
had no switch at any level.

The plugin is pip-installed from a git pin, so a site-packages patch does not
survive a rebuild. Forking it was therefore a one-line change to the install
URL, which is what was done: `aaka3207/cognee-integrations`, branch
`hermes/recall-defaults`, pinned by sha in the Dockerfile.

**Two of the four problems turned out to be already fixed upstream.** The fork
is a clean sync of upstream `main`, 56 commits past what the image had pinned,
and those commits include:

| §  | problem | outcome |
|---|---|---|
| 4.4, 5.3 | recall reads the session cache via `scope: auto` | **gone.** `_recall_scope_params` deleted; recall targets `["graph"]` unconditionally, and a `scope` argument from an older caller is ignored. |
| — | the plugin pinned `cognee==1.5.4` against a 1.6.0 backend | **closed.** Plugin 1.3.0 pins `cognee==1.6.0`. |

**Two needed writing.** Both default to today's behaviour, so nothing changes
until the key is set:

| §  | problem | key |
|---|---|---|
| 4.2, 4.5, 5.2 | no config default for `search_type`, so a forgetful caller gets an LLM completion that paraphrases identifiers | `search_type` |
| 5.3 | `sync_turn` writes every turn to the session cache with no switch; `improve_on_end: false` is not one | `session_writes` |

Plus the `memory.txt` upload-name fix (upstream #436), moved out of the
in-image patch and into the plugin's own source, and the `GRAPH_COMPLETION`
literal behind `auto_route: false` given a name. §4.5 stays true: `auto_route:
false` is still a trap, deliberately unchanged.

**What this does not fix.** The graph is still what `cognee-graph-analysis.md`
§6 measured — 78% singleton entities, 2,273 predicates. Retrieval still rides
on the embeddings and the extraction LLM. That is the open decision in §0, and
this work does not settle it; it makes the store behave predictably enough that
the decision can be made on evidence rather than on noise.

Operational detail — the config keys, the pin, the guard script — is in
`cognee-operations.md` §5 and §7.

## 15. The prompt layer, 2026-09-27

Steps 1 and 2 of §0's three-things list are done. This section records what
changed, and the larger thing found while doing it: **the prompt was never the
only place memory instructions come from.**

### What shipped

| file | before | after |
|---|---|---|
| `memories/MEMORY.md` | 12 entries, 2,163 B, all four tiers mixed | 2 entries, 475 B |
| `memories/USER.md` | 12 entries, 1,361 B | 11 entries, 1,264 B |
| `SOUL.md` | one memory layer named, one instruction | §11 read/write routing |
| `notion-knowledge-capture` / `notion-cli-operations` skills | 4 references routing writes to Mnemosyne | 0 |

Four preferences and two Notion pointers were promoted out of tier 1 into tier 3
**and verified retrievable before anything was deleted** -- the shared store is
the only copy now, so the order mattered. Tier 1 gained one standing instruction
stating its own boundary, so the file records why it must stay small.

Net change to the always-injected prompt: **+226 bytes.** The routing section
costs 1,914 B more than what it replaced; `MEMORY.md` gave back 1,688 B.

Two dead-system findings, same shape as the Mnemosyne one:

* `SOUL.md` mandated loading `gbrain-knowledge-operations`, which **does not
  exist** in `/opt/data/skills` (166 skills installed). Career conversations were
  instructed to load something unloadable. GBrain is retired; all references
  removed, and no replacement retrieval layer was invented, so career now
  specifies only its hub and SOP. **That is a real gap, not a fix.**
* Cron `e4a9af1d3ed2`, "Career Hub -> GBrain migration dry-run report", was still
  enabled on `0 9 * * 1`, producing output as late as 2026-09-21.

A prompt that names a missing skill fails *silently* -- the agent simply cannot
comply. Retiring a system means grepping the injected prompt, the tier-1 files,
the skills tree and `cron/jobs.json`, not just the code. A check that every
backticked skill name in `SOUL.md` resolves under `/opt/data/skills` is cheap and
found this one.

### There are four injected memory layers, not two

This is the part that matters more than the sort. Asked whether Hermes also has
automatic injection alongside the instructed `cognee_recall`, the answer is yes,
and two of the layers were undocumented here:

1. `provider.system_prompt_block()` -- static `# Cognee Memory`: mode, dataset,
   tool list, plus a **memory steer** (`memory_steer: True`, `memory_steer_text`
   empty so the built-in text is live).
2. `provider.prefetch()` / `_run_layered_prefetch` -- dynamic `## Cognee Memory`,
   **once per prompt**, background thread, 20s budget.
3. `SOUL.md`'s own section.
4. `memories/MEMORY.md` + `USER.md`.

Measured for one query, the injected block's `text` was **17,830 characters,
about 4,500 tokens**:

| span | content |
|---|---|
| 0 - 5,454 (31%) | `Previous conversation:` -- 13 QUESTION/ANSWER pairs, only 9 distinct |
| 5,454 - 16,247 | retrieved context |
| 16,247+ | session guidance |

For a question about strength-training progression, the history layer carried
seven unrelated prior questions, including one about Kubernetes ingress
configuration, and repeated one topic three times. **One block contained two
different creation dates for the same page** -- those are prior LLM *answers*,
not stored text.

Three mechanisms worth keeping:

* `_run_layered_prefetch` hardcodes `query_type="HYBRID_COMPLETION"`,
  `scope=["graph"]`, `only_context=True`. **It ignores `search_type`.** The
  CHUNKS default from §14 governs the `cognee_recall` tool and not the always-on
  lane. `only_context=True` does skip the LLM, so the *retrieved* span is
  verbatim -- the history layer is not.
* **`session_writes: false` stopped the writes, not the reads.**
  `SessionQAVector_text` is still 43 and `session_records` 12, and the
  QUESTION/ANSWER shape matches `remember_session`'s payload exactly. They
  survived the 2026-09-26 wipe because `/api/v1/forget` is dataset-scoped and
  these are session-keyed.
* The **steer contradicts `SOUL.md`**: it says store durable knowledge "through
  the cognee tools rather than Hermes' built-in memory", while `SOUL.md` routes
  behaviour notes to `memories/MEMORY.md`. Two layers, opposite instructions.

Consequently `SOUL.md`'s instruction to call `cognee_recall` at conversation
start does not duplicate the auto-block, it **diverges** from it: the tool
returns five CHUNKS rows of verbatim stored text, the lane returns one
HYBRID_COMPLETION item roughly twice the size, a third of it prose. Same
question, two answers, no signal to the agent about why.

**Draining the session tables is not the fix.** It was recommended as the
cheapest win and then withdrawn: it was never established that the history layer
comes from those tables rather than from the graph, where months of cognified
conversational answers already live. The block returned history even with
`session_id=""`. The real levers are the lane's hardcoded search type and its
`top_k`, which means a fork commit, not a one-time delete.

**Correction, 2026-09-29: the hardcoded search type is upstream design, not a
lever.** The plugin CHANGELOG (SDK-741, cognee #5085) and the comment at
`config.py:242` state it outright: on cognee 1.6.0 only a *completion* search
type with `only_context=True` returns the prompt-shaped item -- session history,
then retrieved context, then guidance -- and the lane exists to inject that item
verbatim. `CHUNKS` returns rows, not that item, so making the lane honour
`search_type` would break it rather than fix it. The history layer is intended
too; the CHANGELOG concedes nothing can tell it apart from the retrieved
knowledge. There is also no config key to turn the lane off: `prefetch()` gates
only on `_is_usable()` and the circuit breaker.

What remains a defect is the *content* of the history layer -- stale prior
answers presented as memory -- and its source is still the open question above:
the session tables, or conversational answers already cognified into the graph.
That has to be established before anything is deleted or patched.
(Established 2026-09-30: the session tables. See §17.)

The verbatim rule in `SOUL.md` was corrected the same day for this reason. As
first written it said "recall returns stored text", which licenses exactly the
mistake it exists to prevent. It now names its source: `cognee_recall` returns
stored text, the auto-injected block is a hint about what to check, and
identifiers must be confirmed before use.

### One bug introduced and fixed

The two Notion pointer records were first written with a truncated URL: the
extraction regex was non-greedy and terminated on the first `.`, which in a
`app.notion.so` host is three characters in. Both records stored a bare scheme
and host fragment. **The verification passed** because it asserted only that
`https://` appeared somewhere -- weak enough to be satisfied by the broken value.

Rewritten from the pre-trim backup with an assertion requiring a `notion.so`
host *and* a 32-hex page id. Storage is verified; recall had not returned them
within five minutes, where the original six were retrievable instantly, and
`dataset_pipeline_status` returns empty. Unresolved.

The lesson is about the assertion, not the regex: a check that cannot fail on the
bug you are guarding against is not a check. A pointer record with no resolvable
URL is worse than no record, because it reads as usable.

### Open

* **What actually crosses the tier-3 / tier-4 boundary.** Unresolved, and it is
  the likely source of the contradictory dates. (Partly eased 2026-09-29: a
  pointer now carries its page id as metadata, so it can be followed without
  parsing the text -- §16. The description question is unchanged.) Tier 3 is specified here as
  pointers plus dated assertions, but at least one record written by Claude
  Desktop embeds a *description* of a Notion page's content and its creation
  date, and `notion-cli-operations/SKILL.md:111` instructs exactly that ("record
  its exact Notion URL and a concise description in durable memory"). Copied
  descriptions go stale against a mutable tier 4, and stale copies are what the
  injected history layer then repeats. Needs a decision: pointer-only, or
  pointer-plus-description with an explicit staleness rule.
* ~~The lane's hardcoded `HYBRID_COMPLETION` and `top_k` -- a fifth fork
  commit.~~ Withdrawn 2026-09-29: the search type is upstream design (see the
  correction above). Replaced by: **where does the injected history come
  from** -- session tables or the graph? Answered 2026-09-30: the
  session tables, filled by Desktop's session-less completions; turned off
  with `CACHING=false` (§17).
* Reconciling the memory steer with `SOUL.md`, or disabling it
  (`memory_steer: false`).
* Whether the rewritten pointer records ever become recallable.
* Career has no retrieval layer specified since GBrain was removed.

## 16. Write metadata and search modes, 2026-09-29

### What changed

Cognee 1.6.1 (`topoteretes/cognee#5158`) copies a document's
`external_metadata` onto its chunks. The plugin sent none, so the fork gained a
`write_metadata` key (fork PRs #2 and #3; hermes #39 and #40). With it on, every
permanent write stores:

| key | value |
|---|---|
| `created_at` | UTC time of the write |
| `created_by` | `hermes` (the `created_by` config key) |
| `write_origin` | `cognee_remember` or `hermes_memory_tool` |
| `hermes_session_id` | the Hermes session, for the tier-2 join in §12 |

`cognee_remember` also accepts a flat `metadata` object. Hermes is told to put a
Notion pointer's page id in `metadata.notion_page_id`. A malformed id is
refused rather than stored -- the §15 truncated-URL bug, prevented at write
time.

A `CHUNKS` recall returns the metadata with each result. Completion modes do
not: over HTTP the `/v1/recall` endpoint has no switch to include it. That makes
the dates and pointers available exactly where they can be trusted, and nowhere
else.

Verified on the first live write. Operational detail and rollback are in
`cognee-operations.md` §5 and §10.

### What it does not change

* **The graph is barely used.** `CHUNKS` is vector search over the stored text;
  it never reads the entities and relations cognify extracts. With `CHUNKS` as
  the default, the graph's readers are:
  * the injected block (`HYBRID_COMPLETION`, graph plus LLM), which is the
    lane `SOUL.md` says not to trust;
  * any explicit `GRAPH_COMPLETION` call, or since 2026-09-30 a
    `context_only` recall (§17);
  * possibly Claude Desktop's MCP recall. Its default search type has not been
    checked.
* **Completion modes still paraphrase.** Metadata does not make a synthesised
  answer safe to act on; it only makes the literal path richer.
* **Old records** carry no metadata. **Claude Desktop** writes carry none
  either, because `cognee-mcp`'s `remember` has no metadata parameter.

### The search-mode prompt

`SOUL.md` now explains the two storage forms and the modes, so the agent
chooses rather than following a rule it cannot see the reason for:
* `CHUNKS` for anything acted on or quoted;
* `CHUNKS_LEXICAL` for exact terms (present on 1.6.1, not yet exercised
  here);
* `GRAPH_COMPLETION` for broad questions that join memories, treated as a
  lead only.

### Open

* **Does Hermes ever choose `GRAPH_COMPLETION`?** Count explicit
  `cognee_recall` calls by `search_type` over a week or two. If it almost never
  does, the graph is paid for on every write and read only by the injected
  block, which strengthens the case in §0 for a cheaper backend. Superseded
  2026-09-30 by the `CHUNKS` versus `context_only` comparison in §17.
* Claude Desktop's recall mode and the missing MCP metadata parameter.

## 17. Session memory off, and what the graph is worth, 2026-09-30

### Where the injected history came from

§15 left this open. The answer: **cognee's session cache, filled by completion
recalls that sent no session id.** The pile was Desktop's, not Hermes's.

* Every completion recall (`GRAPH_COMPLETION`, `HYBRID_COMPLETION`, ...) that
  carries no `session_id` saves its LLM answer to
  `default_session_<dataset_id>`. That is table `cache_qa_entries` in
  `/cognee-storage/system/databases/cache.db`, mirrored as
  `SessionQAVector_text` rows in the dataset's Postgres DB.
* The latest writes came from `10.0.1.16`, which is `cognee-mcp` on the
  `coolify` network: **Claude Desktop.** Each stored answer lists the earlier
  answers it read, so Desktop's completions kept reading its own earlier
  answers back in.
* Hermes always sends `hermes_<session>`, and since `session_writes: false` it
  had saved nothing new.
* The 2026-09-27 measurement in §15 (31% history, two contradictory dates)
  probed with `session_id=""`. That resolves to the default session, **so it
  measured Desktop's pile, not Hermes's lane.**
* The source was the session tables, not cognified answers in the graph.
* Entries carry `expires_at` = created + 7 days. The pile ages out by itself.

### The stall: AUTO_FEEDBACK

Hermes's first `context_only` recall (below) took 64.98s. The same request with
a fresh session id took 2s.

* **What it is.** `CACHING` and `AUTO_FEEDBACK` both default to true. With both
  on, every completion recall that carries a session id first runs
  `feedback_detection.analyze_turn_for_session_context`, a structured-output LLM
  call.
* **No timeout.** It fails open only on an error, never on slowness. The logs
  show nothing during the stall.
* **Measured.** Of 74 `cognee_recall` calls in Hermes's `agent.log`, 8 took over
  10s. The per-prompt lane requests finish 25-60s after the turn starts.
  Vector and graph retrieval themselves took about 1.3s. `CHUNKS` calls skip
  this path and take about 0.4s.

### The fix: `CACHING=false`

Set on `cognee-backend` 2026-09-30 16:22Z. It subsumes `AUTO_FEEDBACK=false`,
and it also turns off session history, answer saving and session context.

Nothing here needs session memory:
* Hermes keeps transcripts in hermes-lcm (tier 2).
* `session_writes` was already false.
* The only live writer was Desktop's default-session pile.

| probe after the change | time | history in context |
|---|---|---|
| `context_only` on the session holding the bad Q&A | 1.2-1.4s | none |
| `HYBRID_COMPLETION` with `only_context` (the lane) | 0.5s | none |
| plain `GRAPH_COMPLETION`, no session (Desktop's shape) | 4.0s | not saved |

`cache_qa_entries` stayed at 67 rows and `cache_session_context` at 172 across
every probe. The existing rows are inert and expire by 2026-10-07.

This closes most of the §15 history-layer problem. **The injected block now
carries graph context only.**

### Context-only recall

The completion modes made a graph read cost an LLM paraphrase. Fork PR #4
(`1e01471`, hermes #41) added a `context_only` argument to `cognee_recall`. It
runs `GRAPH_COMPLETION` (or another completion type the caller names) with
`only_context=True`. That returns the related entities, relationships and
stored passages verbatim, and the agent answers from them itself. Verified
live: 9.2k-12k characters of context per call, and no Q&A saved.

**Caveat.** Part of that context is `TextSummary` nodes, which an LLM wrote at
cognify time. Context-only is less paraphrased than a completion, not
paraphrase-free. Anything acted on still goes through `CHUNKS`.

### Extraction never sees the existing graph

**Why the graph splits entities.**
* The extraction LLM sees only `chunk.text`.
* Node ids are `uuid5` of the lower-cased, underscored name.
* So `ameer` and `ameer akashe` become two nodes, and nothing links them.

**Upstream history.** Research report:
`claudedocs/research_cognee_entity_linking_20260929.md`. Upstream never states
why extraction works this way; idempotency and order-independence are the
likely reasons.
* A prefetch POC that added the nearest existing names to the prompt was
  deleted on 2026-08-12.
* PR #4106 (canonicalisation inside cognify) was closed unmerged.
* The maintainers' answer is `consolidate_entities`, a post-hoc merge in 1.6.1.
  It is a poor fit here:
  * it is destructive, with no dry run over HTTP;
  * it keeps no alias record, so the next write re-creates the duplicate;
  * its fuzzy mode merged wrong entities 30-46% of the time.

**The client-side trial.** `/api/v1/remember` takes a per-call `custom_prompt`
that *replaces* the system prompt. So the plugin could send the default graph
prompt plus an "existing entities" list without any server change.
`scripts/cognee/entity_glossary_trial.py` A/B-tested this on throwaway datasets
(2 runs x 12 alias-laden records), since deleted:

| | baseline | glossary |
|---|---|---|
| `ameer` / `ameer akashe` nodes | split 4/2 in both runs | 6/0 and 5/1 |
| `cognee backend` -> `cognee`, `hevy app` -> `hevy` | separate | merged |
| `ntn cli` / `notion cli (ntn)` | separate | merged in 1 of 2 runs |
| "the user" -> Ameer | never | never |
| wrong merges | -- | none seen |
| entities | 33 | 30, 31 |

No added latency (2-4s per write). The trial's glossary count includes 14
bullet lines from the default prompt itself. That is a display bug and does
not affect the merge counts.

**The verdict: it helps, but not enough to be the only fix.** Parked, not
forked. Untested: choosing the top-k names at the scale of the real `shared`
graph.

**Name lookup over HTTP works, so no core change would be needed.**
* All names: `POST /api/v1/recall`, `search_type: "CYPHER"`,
  `MATCH (n:Node) WHERE n.type = 'Entity' RETURN n.name`. Returned all 231
  `shared` entities in 0.9s. `ALLOW_CYPHER_QUERY` defaults to on, and the query
  is raw.
* Nearest names: `GET /api/v1/visualize/json` with `query`,
  `neighborhood_depth=1` (the minimum) and `neighborhood_seed_top_k`. Takes
  about 0.5s. It is a UI endpoint, so it is less stable.
* Dead end: `PUT /datasets/{id}/schema` stores a `custom_prompt` that the write
  path never reads.

### Graph versus CHUNKS on one real question

On the question that started this (the ENT appointment and the PTO it needs):
* `CHUNKS` ranked the same records the graph context surfaced, in its top 3.
* The graph did not find anything `CHUNKS` missed. It only left out the
  irrelevant tail.
* An earlier claim that the graph surfaced the PTO record and `CHUNKS` would
  miss it was wrong: `CHUNKS` ranked it #3.

One question settles nothing either way.

**Seventeen questions, 2026-10-01** (`scripts/cognee/graph_vs_chunks.py`, results
in §11 of `cognee-operations.md`): `CHUNKS` answered all 17, graph context 14,
and the hybrid block 16, but only because it leads with chunk passages. The
graph found nothing `CHUNKS` missed. The store is small (32 records), so this
is an early reading, not the verdict.

### Plan

* `CHUNKS` stays the default for anything acted on.
* `context_only` replaces plain `GRAPH_COMPLETION` for broad and relational
  questions. The `SOUL.md` line saying so was handed over on 2026-09-30; it is
  not confirmed applied.
* Compare `CHUNKS` with `context_only` on real questions until about
  2026-10-14. Then record the graph's keep-or-drop in §11 of
  `cognee-operations.md`. If the graph earns nothing, §0's case for a cheaper
  backend stands.
* 2026-10-01: the evidence so far does not support the `context_only` bullet
  above. On 17 questions `context_only` answered 14 to `CHUNKS`' 17, including
  the broad and relational ones, and on "health goals" it was worse than
  `CHUNKS` (§11 of `cognee-operations.md`). Use `CHUNKS` for broad questions too
  until the 2026-10-14 re-run says otherwise.

### Consolidation (proposed 2026-10-01, not built)

The concern: as `shared` grows, `CHUNKS` keeps only the top 5, and three things
push the right record out:
* superseded versions pile up, because records are never edited (the ENT date
  already has three);
* records that start alike compete ("[claude-desktop] Ameer's X, exported...",
  nine near-identical therapy "Pointer" records on 2026-10-01);
* broad questions have no single record to find ("health goals").

Better ranking only sorts the pile. Consolidation shrinks it, like Mnemosyne's
`sleep` (compress old memories into summaries) and `invalidate` (mark one
superseded, linked to its replacement). Cognee has neither:
`distill_sessions` turns sessions into records, not records into fewer records,
and `consolidate_entities` merges graph nodes destructively.

So it would be a scheduled job, shaped like the existing "Cognee to Notion
filing" cron:
1. Group `shared` records by topic (health, ENT, PTO, Joyce, finances).
2. Per group, an LLM writes one current-state record. It lists the ids and
   dates of the records it replaces, says what changed ("Oct 20 -> Nov 2, per
   2026-09-30"), and points to the Notion page as the source of truth.
3. The replaced records move to an `archive` dataset instead of being deleted:
   recall searches `shared` only, so they stop competing, but a bad merge can
   be undone.

The risk is the one `CHUNKS` was chosen to avoid: an LLM rewrite paraphrases
names, ids and dates. Guards:
* the script checks that every identifier, URL and date in the sources appears
  verbatim in the merged record before anything is archived;
* every merge is reviewed by the user before the archive step, which is a
  production write and gated anyway.

First step: a dry run on `shared` that writes nothing and shows proposed merges,
starting with health. Then a throwaway-dataset run, re-scored with
`graph_vs_chunks.py`, before anything touches `shared`.

### Open

* Whether `SOUL.md` carries the `context_only` guidance.
* Whether Desktop's recall should move to `context_only` or stay on `CHUNKS`.
  Its completions no longer pollute anything, but they still paraphrase.
* The glossary commit: revisit only if the comparison says the graph is worth
  keeping.
* Consolidation: whether to build the dry run, and whether consolidated records
  should replace the graph's role for broad questions.

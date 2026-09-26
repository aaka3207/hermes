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

# What is actually in the imported corpus

Measured 2026-09-25 against the 1,433 records in the live `shared` dataset,
read-only. Reproduce with `scripts/cognee/corpus_shape.py`.

This exists because the re-seed decision was blocked on not knowing the shape
of the data or what was worth keeping. It turns out to be largely a **mechanical**
question: two prefixes separate durable from disposable without an LLM.

---

## 1. The short version

**36% of the corpus is worth keeping** -- 517 of 1,433 records. The rest is
mostly raw conversational turns that Mnemosyne's `working_memory` tier held
transiently and the export wrote into permanent storage, plus a smaller
population of agent worker prompts that leaked in through ambient capture
(§6).

The corpus is also **three months old, not two years** -- 2026-07 (37),
2026-08 (634), 2026-09 (709). An earlier claim in
`docs/cognee-graph-analysis.md` §5 said otherwise and has been corrected.

## 2. Tiers

| tier | records | |
|---|---:|---|
| `working_memory` | 1,125 | 78.5% -- Mnemosyne's *transient* tier |
| `episodic_memory` | 252 | 17.6% |
| (no header) | 53 | mostly session transcripts |
| `memories` | 3 | |

That `working_memory` is 78% of a permanent store is the headline. It is the
tier designed not to persist.

## 3. The mechanical markers

Six populations, separated by prefix, tier and length alone:

| population | records | % recs | % of all text | median len |
|---|---:|---:|---:|---:|
| short turn (<80 chars) | 512 | 35.7% | — | 37 |
| unmarked, ≥80 chars | 403 | 28.1% | — | 343 |
| `episodic_memory` | 252 | 17.6% | — | 311 |
| discord relay turn | 164 | 11.4% | — | 180 |
| model-slot | 59 | 4.1% | — | 204 |
| **session transcript** | **43** | **3.0%** | **28.9%** | **1,067** |

### Session transcripts are the biggest single lever

43 records -- **3% of the corpus and 29% of all the text in it**. They are
whole conversation logs:

```
Session ID: hermes_20260924_105222_d4ffb2  Question:   Answer:
User: where can I get an iphone 17 unlocked with 36 month financing ...
```

The largest is 21,341 characters. These are logs, not facts. Dropping them
removes nearly a third of the extraction volume at a cost of 3% of the records,
and removes the only records large enough to be split across chunks.

### Short turns are questions with no answers

512 records, median 37 characters:

```
The user asked, "Is Mnemosyne working?"
The user asked whether Mnemosyne is working and whether it has a visualizer.
```

A question recorded as a memory. Each produces one-off entities that connect to
nothing, which is very likely a large share of the 78% singleton-entity rate in
`cognee-graph-analysis.md` §4.

### Discord relay turns are mostly boilerplate

164 records prefixed with the relay's own scaffolding:

```
[Triggering message id: `1545440353924947978` — use as `message_id` for
reply/react/pin via the discord tools.]  [Ameer Akashe] Just the training
```

Measured after stripping that prefix:

| | chars of real content |
|---|---:|
| p25 | 31 |
| median | **52** |
| p75 | 95 |
| max | 387 |

**57% carry under 60 characters of actual content** under ~115 characters of
scaffolding. Most are short turns wearing a costume; stripped, they fall into
the population above.

### Model-slot records are the densest durable signal

59 records in Mnemosyne's canonical form:

```
model:project::Dinefile Hermes profile confidence=0.93: Dinefile work should
use a dedicated Hermes profile with its own workspace containing the
repository and project documentation.
```

Structured, declarative, already distilled. These are exactly what a memory
graph wants, and they are 4% of the corpus.

## 4. The one population that needed reading

`unmarked, >=80 chars` (403 records) is the only group the markers cannot
sort. It was resolved in two steps.

### First, by hand -- and the hand estimate was wrong

40 records were sampled evenly and read. That reading is where the
`model:x::y confidence=` and `[Triggering message id:` markers came from, so it
earned its keep. But the durability rate it produced did not survive scrutiny:
"12 of 23 non-Discord records were durable, so ~50%" **conflated populations**,
because 7 of those 12 were `model:` records, which are their own population and
already counted as durable. For the unmarked group alone it was closer to 5 of
17.

### Then, by classifier, three times

All 403 were labelled against one rubric (`scripts/cognee/classify_unmarked.py`),
and the whole run was repeated three times because a single pass produces a
number without saying whether the number repeats.

**The aggregate repeats. The individual records do not.**

| pass | keep |
|---|---:|
| 1 | 220 (54.6%) |
| 2 | 225 (55.8%) |
| 3 | 222 (55.1%) |

| agreement across 3 passes | records | |
|---|---:|---|
| stable KEEP (3/3) | 187 | 46.4% |
| stable DROP (3/3) | 142 | 35.2% |
| **unstable** | **74** | **18.4%** |

So the answer is not a percentage, it is a band: **187 settled keep, 142
settled drop, 74 that need a human.** Scoring the run against the 13 hand
labels: 9 agree, 3 disagree, 1 not found -- and **all three disagreements are
classifier-keep against hand-drop**, so the rubric is more permissive than the
hand pass was. On review it is probably right about two of the three.

### The 74 collapse to a handful of decisions

They are not 74 independent judgements. The classifier wavers on whole *kinds*
of record (`scripts/cognee/triage_review.py`):

| cluster | records | lean |
|---|---:|---|
| prose (judge individually) | 41 | 18 keep / 23 drop |
| agent worker prompt (Gmail / Composio) | 14 | 7 / 7 |
| scheduled cron-job prompt | 8 | 1 / 7 |
| async delegation / system notice | 8 | 4 / 4 |
| workout / health observation | 2 | 2 / 0 |
| Notion page/bookmark created | 1 | 0 / 1 |

**30 of the 74 are operational prompt text that leaked into memory** -- Gmail
monitor worker prompts, cron-job preambles, async-delegation notices. Those are
one policy call, not thirty. Whether a reusable worker prompt counts as a
durable memory is a real question, but it is *one* question.

That leaves 41 genuine one-offs to read.

## 5. Where that lands

| | records |
|---|---:|
| mechanically durable (model-slot + episodic) | 311 |
| classifier-settled keep, from the unmarked 403 | 187 |
| human-reviewed keep, from the unstable 74 | 19 |
| **settled keep** | **517** |
| mechanically disposable (short + transcript) | 555 |
| discord relay (57% near-empty once stripped) | 164 |

So **517 of 1,433 are keeps (36%)**. The 74 undecidable records were reviewed
by hand; §6 records what that resolved to.

A re-seed of ~520 records is **~1.3 hours** at the measured ~9s per write,
against ~3.5 hours for the whole corpus, and the graph should improve by more
than the ratio suggests because the dropped populations are disproportionately
the ones generating singleton entities and junk hubs.

## 6. The 74, resolved

Reviewed by hand: **19 keep, 55 drop.** The record text is deliberately not in
this repo -- it is raw personal memory, including health -- so what follows is
the decision, not the data.

**31 of the 74 were one call, not thirty-one.** Gmail worker prompts (16),
cron-job preambles (8) and async delegation notices (7): operational text that
leaked into memory through the ambient capture path. All dropped. Their source
of truth is the cron definition or the prompt file, so a copy in memory goes
stale silently, and they are self-similar enough to crowd real records out of
retrieval -- the `mnemosyne` / `working_memory` hub problem
(`cognee-graph-analysis.md` §4) arriving through a different door.

This is also *why* the classifier could not settle them. Same text, both
verdicts, across passes: "is a reusable instruction a memory?" is a policy
question, and nothing in the record answers it.

**The remaining 43 split 19 keep / 24 drop.** The keeps are standing
preferences, project definitions, one MCP configuration and one medical
assessment. Several were rewritten rather than kept verbatim -- where the
durable fact was one clause inside a paragraph of conversation, the fact was
extracted and the paragraph dropped.

**The classifier's bias, measured against the hand pass.** It disagreed on 14
of 74, and the disagreements were directional: it kept operational logs and
one-off command output that read as "concrete", and it dropped personal
preferences stated casually (*"I just want max side income minimal time used"*).
Good at spotting noise, weak at recognising an offhand preference. Worth knowing
before reusing `classify_unmarked.py` on another corpus -- the rubric needs
strengthening on the second failure mode, not the first.

**One judgment call that may need reversing.** Six drops describe Mnemosyne
internals -- sleep-model refresh, backup policy, prefetch measurements,
`sync_turn` semantics, an unused fork. They were dropped because the personal
profile's provider is now `cognee`. But `mnemosyne-mcp` is still a running
container and the shared surface still exists, so if Mnemosyne is touched again,
two of the six (its backup policy, and the "this fork is dead" marker) are worth
restoring. `memory_only` retraction keeps that reversible.

### Executed 2026-09-26

The mechanical cut was run: **662 records deleted, zero failures, 28 minutes**
(~2.8s each), via one `POST /api/v1/forget` per record with
`{"datasetId", "dataId"}`. Hermes stayed in use throughout -- per-record
retraction needs no restart and never empties the graph.

| marker | before | after |
|---|---:|---:|
| total records | 1,455 | **800** |
| short turn (<80) | 512 | 0 |
| discord relay | 164 | 71 |
| ... empty once stripped | 93 | 0 |
| session transcript | 52 | 14 |
| test artifact | 5 | 0 |
| episodic_memory (durable) | 252 | 252 |
| model-slot (durable) | 59 | 59 |
| unmarked >=80 | 403 | 404 |

Both durable populations are untouched, which is the check that matters. The
unmarked count rose by one during the run -- live use.

Reference-counted retraction behaved as `cognee-consolidation-design.md` §4
describes: the first deletion alone pruned 23 orphaned `EdgeType` nodes, so the
graph sheds structure with the documents rather than accumulating dangling
types.

**Rollback**: every record's full text was dumped before the run to
`~/cognee-corpus-backup-20260925.json` on the host (1,447 records, 1.1 MB),
alongside `~/cognee-drop-candidates-20260925.json`. Nothing here is one-way.

**14 transcripts reappeared during the 28 minutes.** They are written by
`improve()`'s `persist_session_qa` stage. That stage is identified but **not
switchable** -- cognee classes it as *fatal* and refuses to disable it, taking
the backend down if you try (`cognee-consolidation-design.md` §2.1). So this
cut is a sweep, and for now a sweep is the only available treatment.

## 7. Caveats

* **18.4% of the unmarked population is genuinely undecidable by classifier**
  and is reported as such rather than rounded into the keep or drop pile.
* **Classification is not reproducible at the record level.** Three passes over
  identical input disagreed on 74 records. Any future automated pass over this
  corpus should be run more than once and scored for agreement, not trusted on
  a single run -- the same non-determinism that makes graph repair
  non-convergent (`cognee-consolidation-design.md` §4).
* **Discord relay turns are not uniformly disposable.** 43% carry over 60
  characters of content, and some of those are real. Stripping the scaffolding
  and re-running the length test is the cheap way to sort them.
* **Nothing here judges *staleness*.** A durable-looking record from July may
  have been superseded in September; that is the consolidation problem
  (`docs/cognee-consolidation-design.md`), not the triage problem.
* **`memory_only` retraction makes this reversible.** Per
  `cognee-consolidation-design.md` §4 the raw records are retained on disk
  (1,447 files, 155 MB), so a cut that turns out wrong can be re-ingested
  without going back to Mnemosyne.

## 8. Related

* `docs/cognee-graph-analysis.md` -- what the graph built from this corpus
* `docs/cognee-consolidation-design.md` -- retraction, and the consolidation sketch
* `scripts/cognee/extraction_prompt.txt` -- the replacement extraction prompt
* `scripts/cognee/corpus_shape.py` -- reproduces the structural counts
* `scripts/cognee/classify_unmarked.py` -- labels the unmarked population
* `scripts/cognee/triage_review.py` -- scores stability, clusters the remainder
* `scripts/cognee/backup_corpus.py` -- dumps every record's raw text before a cut
* `scripts/cognee/score_drops.py` -- emits deletion candidates with data_ids
* `scripts/cognee/forget_drops.py` -- executes the retraction (dry run by default)

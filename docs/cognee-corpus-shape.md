# What is actually in the imported corpus

Measured 2026-09-25 against the 1,433 records in the live `shared` dataset,
read-only. Reproduce with `scripts/cognee/corpus_shape.py`.

This exists because the re-seed decision was blocked on not knowing the shape
of the data or what was worth keeping. It turns out to be largely a **mechanical**
question: two prefixes separate durable from disposable without an LLM.

---

## 1. The short version

**About 35% of the corpus is worth keeping**, with a further 5% needing a
human call. Most of it is raw
conversational turns that Mnemosyne's `working_memory` tier held transiently
and the export wrote into permanent storage.

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
| **settled keep** | **498** |
| unstable, needs a human | 74 |
| mechanically disposable (short + transcript) | 555 |
| discord relay (57% near-empty once stripped) | 164 |

So **498 of 1,433 are settled keeps (35%)**, with 74 records -- an afternoon's
reading, and mostly one policy call -- as the entire remaining ambiguity.

A re-seed of ~500 records is **~1.3 hours** at the measured ~9s per write,
against ~3.5 hours for the whole corpus, and the graph should improve by more
than the ratio suggests because the dropped populations are disproportionately
the ones generating singleton entities and junk hubs.

## 6. Caveats

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

## 7. Related

* `docs/cognee-graph-analysis.md` -- what the graph built from this corpus
* `docs/cognee-consolidation-design.md` -- retraction, and the consolidation sketch
* `scripts/cognee/extraction_prompt.txt` -- the replacement extraction prompt
* `scripts/cognee/corpus_shape.py` -- reproduces the structural counts
* `scripts/cognee/classify_unmarked.py` -- labels the unmarked population
* `scripts/cognee/triage_review.py` -- scores stability, clusters the remainder

# What is actually in the imported corpus

Measured 2026-09-25 against the 1,433 records in the live `shared` dataset,
read-only. Reproduce with `scripts/cognee/corpus_shape.py`.

This exists because the re-seed decision was blocked on not knowing the shape
of the data or what was worth keeping. It turns out to be largely a **mechanical**
question: two prefixes separate durable from disposable without an LLM.

---

## 1. The short version

**Roughly a third of the corpus is worth keeping.** Most of it is raw
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

`unmarked, ≥80 chars` (403 records) is the only group the markers could not
sort, so 40 were sampled evenly across the corpus and read by hand.

Of the 23 non-Discord records in that sample, **12 were clearly durable** --
project decisions, standing instructions, factual records:

```
DineFile (formerly recd) uses Vercel for hosting, Supabase for
backend/auth/database/storage, PostHog for product analytics, Resend for
email, and Upstash Workflows for durable workflow orchestration.

use the hermes desktop browser from now on (if you know you're on hermes desktop)

I want you to start making a weekly report for me of my workouts for that
week. posting to the discord channel
```

The rest were conversational turns -- opinions mid-discussion, one-time task
instructions, questions. A handful were genuinely borderline (a preference
embedded in an aside).

So roughly **50% of this population is durable**, or about 200 records. That
figure comes from a 23-record read and is the softest number in this document.

One sample was a test artifact: *"the agent wants you to ask it to 'remember
that this is an author-attribution test row.'"*

## 5. Where that lands

| | records |
|---|---:|
| mechanically durable (model-slot + episodic) | 311 |
| judged durable (~50% of unmarked ≥80) | ~200 |
| **estimated keep** | **~510 of 1,433 (36%)** |
| mechanically disposable (short + transcript) | 555 |
| discord relay (57% near-empty once stripped) | 164 |

A re-seed of ~510 records is **~1.3 hours** at the measured ~9s per write,
against ~3.5 hours for the whole corpus -- and the graph should improve by more
than the ratio suggests, because the dropped populations are disproportionately
the ones generating singleton entities and junk hubs.

## 6. Caveats

* **The ~50% durable rate for the unmarked population rests on 23 hand-read
  records.** Everything else here is a full-corpus count.
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
* `scripts/cognee/corpus_shape.py` -- reproduces every count above

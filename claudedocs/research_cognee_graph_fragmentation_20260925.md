# Why Cognee 1.6.0 builds a fragmented graph from short records

**Date:** 2026-09-25 · **Depth:** standard (2 hops) · **Confidence:** high
**Question:** Are the 2,273 predicates / 78% singleton entities / date-entities
/ one-chunk-per-record findings in `docs/cognee-graph-analysis.md` expected, and
what knobs exist?

**Sources:** Cognee 1.6.0 source read directly from the running backend
(primary evidence), plus Context7 `/topoteretes/cognee` docs. Where the two
disagreed, the running source wins — it is the code actually executing.

---

## Executive summary

**All four findings are by design, not misconfiguration.** Three have real
fixes; one is intrinsic to the corpus.

| finding | cause | fixable? |
|---|---|---|
| 2,273 predicates, 1,609 singletons | prompt permits **any** snake_case name; no vocabulary anywhere | only via custom `graph_model` |
| `ameer` ≠ `ameer akashe` | coreference is prompt-scoped to **one chunk**; no alias mechanism exists | **no — not in 1.6.0** |
| dates as entities | prompt instructs basic types including `Date` | yes — prompt swap / graph_model |
| one chunk per record | `chunk_size` auto-sized to LLM max; records are shorter | no, and correct |

**Two corrections to what I assumed before reading the docs and source:**

1. I expected an ontology to fix predicate fragmentation. **It does not** —
   cognee's docs state "relationship names are never checked against the
   ontology's object properties."
2. I then expected an ontology to fix the identity split. **It does not
   either**, for this data. Matching is `difflib` at a **0.8** cutoff against
   *URI fragments only* — `build_lookup` never reads `rdfs:label` or any alias
   property, so aliases cannot be declared. Measured on your actual names:

   | pair | ratio | result |
   |---|---|---|
   | `ameer` / `ameer_akashe` | 0.588 | no match |
   | `gbrain` / `gbrain_life` | 0.706 | no match |
   | `notion` / `notion_cli_(ntn)` | 0.545 | no match |

   All below threshold. Cognee's marketing page claims ontology matching
   eliminates "cross-document duplicates" — true only for variants that
   already score ≥0.8, which yours don't.

**All four knobs are reachable over HTTP on `/api/v1/remember`** — the exact
endpoint Hermes uses. The plugin simply never sends them.

---

## 1. Predicate fragmentation is the prompt's design

`llm/config.py:165` → `graph_prompt_path: str = "generate_graph_prompt.txt"`.
That default prompt's *entire* constraint on relationship naming is one line:

```
**Naming Convention**: Use snake_case for relationship names, e.g., `acted_in`.
```

No enumerated set, no controlled vocabulary, no reuse instruction. An
open-vocabulary extractor asked to name a relation between two nodes in a
single short record will invent `redistributed_energy_into`. 1,609 one-off
predicates is the expected output of that instruction, not a malfunction.

**Swapping prompts barely helps.** The five shipped variants (`_simple`,
`_strict`, `_guided`, `_oneshot`, base) were checked; `_strict` is the most
constrained and still only says:

```
- Keep relationship types semantically clear and consistent.
- Avoid vague or ambiguous relation names like "related_to" ...
```

Still open-vocabulary. *(confidence: high — read all variants)*

**Nothing normalizes predicates downstream either.** The only transform is
`modules/engine/utils/generate_edge_name.py`, in full:

```python
def generate_edge_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace("'", "")
```

Cosmetic. `recorded_at` and `recorded_on` are, and remain, two predicates.

### The one real fix: a custom `graph_model`

`cognify.py:112` takes `graph_model: BaseModel = KnowledgeGraph`. Passing a
custom Pydantic `DataPoint` subclass replaces freeform extraction entirely —
**relationship names become your model's field names**, so the vocabulary is
closed by construction. `extract_graph_from_data.py` branches on
`if not issubclass(graph_model, KnowledgeGraph)` and takes a different path.

Cost: you must define the schema up front, and anything outside it is not
extracted. For a personal memory corpus spanning health, career, travel,
infrastructure and preferences, a schema tight enough to fix the vocabulary
is probably too tight to hold the content. **This is a genuine trade-off, not
a free win.**

## 2. Entity aliasing: coreference is per-chunk

The prompt does ask for coreference resolution:

> If an entity is mentioned multiple times in **the text** but referred to by
> different names or pronouns, always use the most complete identifier

"The text" is one chunk. Each of your ~1,432 records is its own chunk and its
own LLM call, so the model **never sees two records together** and cannot know
`ameer` in record 40 is `ameer akashe` in record 900. Combined with
name-derived deterministic ids, the split is permanent.

### Fix: the ontology resolver — and only for this

```bash
ONTOLOGY_RESOLVER=rdflib
ONTOLOGY_FILE_PATH=/path/to/ontology.owl
MATCHING_STRATEGY=fuzzy      # ~80% similarity
ONTOLOGY_MODE=...
```

`RDFLibOntologyResolver.find_closest_match(name, category)` fuzzy-matches
extracted names to ontology individuals, and
`construct_data_points_and_edges_with_ontology` **collapses nodes matched to
the same ontology entity** (`surviving_node_id_by_collapsed_node_id`). That is
exactly the `ameer`/`ameer akashe` merge.

**But its docstring is explicit about the limit:**

> Strict mode is entity-grounding only: a node is retained when the ontology
> matched EITHER its type against a class OR its name against an individual —
> an unknown entity with a recognized type survives. Nodes with neither match
> are dropped along with their edges. There is no domain/range/cardinality/
> disjointness reasoning, and **relationship names are not checked against the
> ontology**.

So: ontology fixes entities, not predicates. *(confidence: high — verbatim
from the executing source)*

⚠️ **`ONTOLOGY_MODE=strict` is destructive here.** It *drops* nodes matching
neither a class nor an individual. Against an open-ended personal corpus that
silently discards whatever the ontology failed to anticipate. Use the
non-strict (enrich) mode.

## 3. Dates as entities

The prompt tells the model to label nodes with "basic or elementary types",
and `_simple` names them: `'Person'`, `'Date'`, `'Organization'`. Dates
becoming entities is the instruction working as written, not drift.

Fixable via a custom `graph_model` (a date becomes a *field*, not a node) or a
custom prompt. Worth doing: date-nodes are false bridges, and contradiction
detection reads the 1-hop neighbourhood they pollute.

## 4. One chunk per record is correct

`chunk_size` defaults to `get_max_chunk_tokens()` — derived from the model's
context window, far larger than a Mnemosyne record. One chunk per record is
right, and lowering `chunk_size` would only fragment records further. Leave it.

The real consequence isn't chunking, it's that **per-record extraction means
every cross-record link is an accident of identical naming** — which is why
§4 of the graph analysis found 78% singletons. Nothing in the pipeline does
cross-document entity linking.

## 5. What's reachable from Hermes

`/api/v1/remember` accepts as Form fields: `node_set`, `chunk_size`,
`ontology_key`, `graph_model`. All four. The `cognify` endpoint takes the same
set.

The Hermes plugin's `_remember` sends only `datasetName` and `session_id`, so
none are currently used. Adopting any would require either patching the plugin
(now an established pattern — `docker/cognee-remember-filename.py`) or seeding
out-of-band.

---

## Recommendation for this corpus

Ranked by value-per-effort:

1. **Seed-time text hygiene — do this regardless.** Strip `mnemosyne`,
   `working_memory` and tier vocabulary from record text before ingesting.
   They are hubs #2 and #3 in your graph purely because the *export format*
   put them in the text. No config, no upstream change, and it removes 579
   records' worth of meaningless linkage.
2. **Normalise names in the text at seed time, not via ontology.** Since
   there is no alias mechanism and 0.8 fuzzy matching misses your variants,
   the only thing that actually merges `ameer` and `ameer akashe` is writing
   one spelling into the record text before ingesting. Same for `gbrain` /
   `gbrain_life`. A find-and-replace over the export beats any config here.

   An ontology is still worth adding *later* for type grounding — it is
   additive in the default **annotate** mode (unmatched entities are kept with
   `ontology_valid = False`) — but it will not fix the splits, so do not
   sequence work behind it.
3. **Accept the predicate fragmentation.** Given the corpus breadth, a
   `graph_model` tight enough to close the vocabulary would exclude too much.
   Retrieval already routes through LLM synthesis over embeddings, which does
   not traverse predicates — so fragmentation costs little *today*. Revisit
   only if you start querying by relationship type.
4. **Do not lower `chunk_size`.** Correct as-is.

The honest framing: Cognee is doing what a per-document open-vocabulary
extractor does. The graph is weak on this corpus because 1,500 short,
independently-written records are close to the worst case for that design —
there is little intra-record structure to extract and no mechanism for
cross-record linking. The synthesis quality measured in
`cognee-graph-analysis.md` §5 is real, but it comes from embeddings + LLM, and
that would survive even if the graph were discarded.

## Confidence and gaps

| claim | confidence | basis |
|---|---|---|
| prompt is the source of freeform predicates | **high** | read the executing prompt file |
| ontology does not constrain predicates | **high** | verbatim source docstring |
| `generate_edge_name` is cosmetic only | **high** | 2-line function read in full |
| custom `graph_model` closes the vocabulary | **high** | `extract_graph_from_data` branch + docs |
| strict ontology mode drops unmatched nodes | **high** | source docstring |
| knobs exposed on `/api/v1/remember` | **high** | router source |
| ontology will **not** merge your name variants | **high** | docs give the 0.8 difflib cutoff; `build_lookup` reads URI fragments only; ratios computed on the real names |
| stripping import residue improves recall | **medium** | strongly implied by hub analysis; unmeasured |

**Not investigated:** whether upstream plans predicate normalization; how
`MATCHING_STRATEGY` thresholds behave on short personal-name strings; whether
re-ingesting with an ontology rewrites existing nodes or only affects new
writes (**this matters before any re-seed** — assume it affects new writes
only until verified).

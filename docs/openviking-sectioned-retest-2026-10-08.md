# OpenViking Career pilot: sectioned re-import and retest, 2026-10-08

**Status: measured, small sample.** Ten hand-picked prompts, 14 expected page occurrences, one run per tool.
It does not show that either store is better. Notion was not touched; the original pilot was not touched.

## What was done

* Staged the 184 pilot `*.md` files (sha256 checked against `metadata/manifest.json`, 0 mismatches), zipped them,
  and imported with `add_resource` (zip via `temp_upload`) into a fresh destination,
  `viking://resources/pilots/career-pilot-sectioned-2026-10-08/`. The Markdown parser split long files at headings.
* Result: 547 files in 240 directories, 240/240 directories with `.abstract.md`. Only 56 of the 184 files were
  over 6 KB, so most files were unchanged.
* `vlm.timeout: 60` was set before the import. No restart during the final run. Whether the timeout helped is
  unproven: the earlier runs needed restarts, and this one did not, but that is one observation.

## Result (Hermes, `find` and list-mode `search`, scored by source page)

| | Sectioned copy | Unsplit copy (earlier retest) |
|---|---|---|
| `find`, expected pages in top 5 | 10 of 14 | 10 of 14 |
| `search`, expected pages in top 5 | 9 of 14 | not run |

The totals match, but the retrieved pages differ.

* **Gained:** apr-01 (LlamaIndex decision framework) is now #1 at 82%, and feb-02 (Shamus McNutt) is #2. Both were
  persistent misses before.
* **Still missing:** apr-03 (April 11-12 entry) and the mar-01 timeline page. The timeline is #2 only inside the
  narrow notes scope.
* **Lost:** jan-01 (10-year plan) and the second, "Dead" John Rood contact row. Checked against
  `run-report-2026-10-07.md` in Hermes's benchmark folder (the first unsplit run, `find` top 5, also 10/14): the
  plan was "present in top 5" and the duplicate John Rood row was #4. Both hit then and miss now. So the tie is
  two gained, two lost. The later unsplit retest of 2026-10-08 was not saved there; it had the same 10/14 total.
* feb-01 Feb. 19 recommendation: #5 in `find`, #6 in `search`.

apr-03's top five are other April tracker and changelog entries (79% each). A near-match titled
"[DELETE] April 11-12, 2026 - Created in error" ranks 13th and has a different Notion id, so it was not counted.

## Reading

Sectioning helps where the miss was a long page with no summary of its own (apr-01). It did not lift the
near-duplicate cases (apr-03, mar-01), where several similar April entries compete at 78-79%. Those look like a
ranking problem, not an ingestion problem. Directory abstracts also take top slots, and later sections lack the
Notion-id header, so scoring has to map each section to its parent page.

Decision rule from the plan: sectioning did not lift all four persistent misses (two lifted, two not, and two
other pages dropped out). So `add_resource` is better than `content/write` for long pages, but it is not
sufficient on its own.

## Next levers, none tried

* ~~Verify that the apr-03 and 10-year-plan pages exist in the sectioned copy.~~ Done: both exist.
  `hub/10-year_personal_plan/10-year_personal_plan.md` carries `source_notion_id: 2f2ebcff…`, and the apr-03 page
  is `changelog/2026-04-12-2026-04-11-12-llamaindex-deep-_a37e2f19/`. Two "[DELETE] April 11-12 ... created in
  error" pages sit beside it (`…delete-april-11-12-2026-create_0a6b8879`, `…_e52bbb85`). So these are ranking
  misses, not import losses, and the deleted-in-Notion duplicates are plausible competitors for apr-03.
* Query-side: put the date and entry title in the prompt; try `search` with a session or target-directory scope.
* Set `vlm.extra_request_body` to `{"reasoning":{"effort":"low"}}` before the next large import (needs a
  container restart, only when idle).

## Follow-up: junk pages deleted, query wording retest (Hermes, same day)

The two "[DELETE] April 11-12 ... created in error" folders were removed from the sectioned copy (logs show an MCP
request and a changelog summary rebuild at 14:51 UTC; I did not delete them myself). Hermes then reran with the
date and entry title in the query, split compound questions, whole sectioned root, no folder target.

* **B2 manual rewrites** (`find` and `search` gave the same ranks): the 10-year plan #1 (jan-01a), Matt Graham #2
  (jan-01b), the April Transition Timeline #4 (mar-01a), both John Rood rows at #2 and #3 (mar-01b). For apr-03 the
  gold folder's own abstract ranked #1, but the page file itself was not in the top five.
* **B1 original prompts**: no lasting change. jan-01 plan, the mar-01 timeline and apr-03 are still absent from the
  top five (apr-03 is outranked by the tracker and "dead-rejected" pages). The deletion did not fix apr-03.
  Hermes could not verify several ranks (source ids not checked on feb-01, feb-02, apr-01), so there is no
  comparable total for this run; the 10/14 and 9/14 figures above stay as the last scored result.
* **Reading:** adding the date and title to the query finds pages that natural prompts miss, but the rewrites
  carry the identifying words, so this is a lever for an agent that rewrites queries, not a better first-shot
  score. Hermes's scoring treated directory abstracts inconsistently (counted for apr-01, not for apr-03).

## Rerank trial (Jev), same day

`ov.conf` got `rerank: {provider: jev, mode: choice, timeout: 30, threshold: -1}` (key not recorded here).
Findings, each checked on the server:

* **`find` never reranks.** The code hard-codes `mode=QUICK` for `find`; only `search` reranks, and only the top
  `2 x limit` vector hits, so a small limit gives the reranker nothing to promote. Telemetry shows `scored = 2 x limit`.
* **Choice scores sum to 1, so the default threshold of 0.1 (and the MCP tool's default `min_score=0.35`, applied
  when none is passed) cuts most results.** Reproduced: threshold 0.35 gave result counts 0,1,2,0,1,2,1,1,1,2, the same
  counts Hermes saw. Fix: config `threshold: -1` and `min_score=-1` on every MCP `search` call. Threshold 0 still drops
  zero-scored candidates (counts 1 to 20).
* **Result (Hermes via MCP, `search` list, limit 20, min_score -1, all 20 results returned):** top-5 10/14,
  top-10 11/14. My direct API runs gave 10 to 11 and 12. The reranker varies by about one page between identical runs.
  Versus vector-only `find` (10/14 top-5), the top-5 total is unchanged: the 10-year plan came in, the Feb. 19
  debrief dropped to #7. Still outside the top 20: the March timeline and apr-03 (apr-03 was #2 in one direct API run
  and absent in Hermes's run).
* **Splitting compound questions helped more than rerank:** all 5 split R2 occurrences were in the top 5
  (timeline #5, both John Rood rows #2 and #4, plan #1, Matt Graham #1).
* **Reading:** no demonstrated gain from rerank on this sample (14 occurrences, one run each). Costs one Jev call per
  `search` and needs the explicit `min_score` override. Keep it only if a larger or real-use sample shows a benefit.

## Diagnosis of the persistent misses (March timeline, apr-03)

Checked on the server, read-only. Both pages are intact and indexed at all three levels; this is ranking, not ingestion.

* **Summaries are generic.** The timeline's `.abstract.md` says "a strategic planning guide for a tech professional
  managing a career transition in April 2026" (no Livefront, no leadership path, no mid-April window). apr-03's says
  "a weekend deep analysis focused on the LlamaIndex role" (no application count). The specifics live only in the
  `.overview.md`.
* **Files are embedded whole.** Default `embedding.text_source` is `content_only`, `max_input_tokens` 4096, so each
  file is one vector of its entire body (from the code, not checked against stored vectors). One line about a
  mid-April review barely moves a 5 KB planning document's vector.
* **Near-duplicates crowd the top.** The April changelog `Detailed_Entries_*` files, monthly entries and tracker
  pages all cover Livefront and the April pipeline and score within 0.03 to 0.05 of each other. Gold scored 0.733
  (#35) against 0.765 for #1 on the timeline prompt, and 0.749 (#19) against 0.792 on apr-03.
* **Rerank only sees the `abstract` field** (generic summary), not the body, which is why it did not help and made
  the title query worse ("April 2026 Transition Timeline": #1 in `find`, #12 reranked).
* **No keyword search:** `search_type: keywords` returns "not supported by the configured vector backend", so hub
  trial test 13 (BM25 vs grep) cannot run on this server.
* Working levers: page title words, one question at a time, dates. Untried: summaries that keep names and dates
  (regeneration risks the slow VLM tail), a backend with full-text indexing.

## Reliability notes

* A restart re-queues the whole tree; never restart mid-import.
* Earlier imports stalled twice (a task stuck at "1 in progress"); a 32-call concurrency test did not reproduce a
  provider hang. Root cause unverified.

## Open cleanups

Rotate the owner key (pasted in chat), the root key, the scratch MetaMCP key and the cognee OpenRouter key; remove
the scratch `claude` user; nightly export of the pilot (the `ov-data` volume is the only copy); remove the
leftover Coolify helper container `kwfbeu8a3g67cifdj9bh641d`. Cognee verdict due about 2026-10-14.

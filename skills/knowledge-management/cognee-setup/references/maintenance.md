# Maintaining the shared store

Load this before proposing or doing a merge or retirement in `shared`, and before
writing a pointer record. State as of 2026-10-01. Every merge or retirement is a
proposal until the user approves it; production writes and deletes follow the rule
in `SKILL.md` (the user first, with the exact command).

## Two kinds of record

- **Pointer.** Names a Notion page. The page is the current state; the record is
  an index hook that says where to look and what the page is good for.
- **Loose durable fact.** No page behind it (a name, a preference, a standing
  instruction). The record itself is the source of truth, so it has to be right.
  Keep these small and one fact each.

## When to propose a merge or a retirement

Propose when one of these holds:

1. **Supersession.** A newer record gives a different value for the same fact (a
   date that moved, a preference that changed). Propose retiring the older one.
2. **Same target.** Two or more records name the same Notion page id or URL and
   say overlapping things. Propose merging them into one pointer record.
3. **Crowding.** A query on the topic returns three or more records from the same
   topic in its top 5, with overlapping content.

Do not propose:

- merging separate loose facts because they share a topic;
- merging records that differ in scope or context, even if the wording is close;
- anything where the relationship is unclear: ask instead. Leaving both records
  costs nothing; a wrong merge does.

Records that carry session provenance may be proposed for merging, but say so in
the question, and the merged record must keep each source's provenance text
verbatim.

Records written by Claude Desktop (prefix `[claude-desktop]`, no session id, no
write metadata) skip the distill job's duplicate check, so most pile-ups start
there.

## Writing a pointer record so it can be found

Recall matches on the meaning of the query, so write the record in the words
someone would use to ask.

- Open with what it is and the page title. Quote the page id and URL verbatim.
- Add a `Good for:` line phrased as the questions it answers, for example
  "what did Katelyn say about X" or "what homework was open".
- Do not copy the page's content. It goes stale. End with "Pointer only: fetch
  the page for current content."
- One topic per record, about 2.5k characters at most. Prefer one index record
  per topic area over one record per page, unless the page is the topic.
- Include who wrote it and the date it was written.

## Merge procedure (after the user approves)

1. **Draft** the merged record or records.
2. **Token check.** Every UUID, 32-hex id, URL, date and quoted title in the
   sources must appear verbatim in the merged set, and nothing in the merged set
   may be missing from the sources (except the consolidation date). Restore any
   quoted retrieval hook verbatim instead of paraphrasing it.
3. **Back up** each source (id, created_at, dataset id, full text) to a file that
   survives a redeploy, not a container's `/tmp`. Confirm no record is missing
   its text.
4. **Write** the merged records with `cognee_remember` into `shared`. Verify
   through the dataset data listing, not by recall: the new rows exist and their
   lengths match.
5. **Retrieval check.** A query on the topic returns the merged record.
6. **Retire** each source with `forget`, passing both `data_id` and
   `dataset_id`. Never pass a dataset alone, and never `everything`.
7. **Verify.** The sources are gone from the listing, the record count equals
   before + merged - sources, and other datasets are unchanged.

Undo: write the backed-up texts again. They get new ids and lose their original
`created_at`.

If your tools do not expose `forget` or the dataset listing, stop at the
proposal and hand the user the exact commands.

## Worked example

2026-10-01: 14 therapy pointer records (all naming the same Psych Notes page) were
merged into 4: an index, session pages and recordings, prep and self-work pages,
and therapy notes kept in other hubs. The token check caught three retrieval
hooks that had been paraphrased. After the merge, "what homework did my therapist
assign" surfaced the sessions record, which the old pointers had missed.

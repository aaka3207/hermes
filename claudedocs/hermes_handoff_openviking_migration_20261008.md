# Hermes handoff: OpenViking migration, 2026-10-08

**Status: executed 2026-10-08.** Hermes saved the consolidated cognee record and recorded the P4 result. This file keeps the
record of what was handed over and what came of it.

## What was handed over

1. A correction for Hermes's Career pilot validation (P4):
   - the checklist is `/opt/data/skills/knowledge-management/knowledge-operations/references/notion-openviking-pilot.md`;
   - the manifest is `viking://resources/pilots/career-pilot-2026-10-07/metadata/manifest.json`; the sectioned copy was imported
     with `source/` only, so it has none (lineage: the 184 files were sha256-checked against the manifest with 0 mismatches
     before the zip import);
   - join manifest to sectioned copy on normalized Notion ID, not on path;
   - backup is a known gap, refresh is absent by design, ACLs are not a gate.
2. A consolidated cognee record of the OpenViking–Notion project (status, pilot copy locations, known gaps, doc locations).

## What happened

- Hermes reconciled the manifest against the sectioned copy: 184 manifest rows, 182 with IDs, 169 unique; sectioned copy 180
  ID headers, 168 unique. The single unmatched ID `340ebcff7b9b81e285f4d750b8c2d41c` is the two "[DELETE] April 11-12, 2026 -
  Created in error" changelog pages, deliberately deleted from the sectioned copy on 2026-10-08 (~14:51 UTC). Score: pass with
  one explained deviation.
- The omissions log (`source/hub/omissions.md` in the original pilot) is stale: it lists 24 hub children as "none fetched",
  but 23 were later imported into `hub-children/` and the 24th (Livefront Bounds Rulebook) is marked imported separately in
  `source/hub/`. It also omits the two deleted-in-error entries. (An earlier version of this note also called its "no fetch
  reported truncation" line wrong, saying the LlamaIndex Post-Sales SA Decision Framework was imported truncated. Corrected
  2026-10-08: the user confirmed the Notion page itself ends mid-sentence at "Reduc…", so that is source incompleteness, not
  import or export loss.) The log is not edited, because its sha256 is in the manifest.
  Decision: no errata file; the corrections live in Hermes's P4 report and the cognee record.
- P4 was recorded in `/opt/data/projects/unified-personal-os/.hermes/reports/openviking-knowledge-architecture-2026-10-07.md`.
- The consolidated record was saved to the self-hosted cognee `shared` dataset at 2026-10-08 18:03 UTC
  (`memory-cddb0a11fb811606`).

## Cognee incident

An earlier draft of the consolidated record landed in the self-hosted `shared` dataset at 17:13 UTC (document
`83680eb1-5f19-4aa0-bbb2-b3292f50140f`) with no Hermes write metadata. Its text matched a draft written through the Cognee
connector in a call the user declined. Cause not confirmed: the connector probably writes to the Coolify backend, and the write
went through despite the rejection. The record was stale (it said the repo docs still needed copying to the server). Hermes
retired it on request; a recall afterwards returned the 18:03 record and not the draft (re-verified from the backend).

## Docs on the server

`/opt/data/projects/hermes-repo-docs/` holds `docs/openviking-analysis.md`, `docs/openviking-hub-trial.md`,
`docs/openviking-sectioned-retest-2026-10-08.md` and `claudedocs/research_openviking_peers_hermes_plugin_20261008.md`, each
byte-identical to the repo copy (sha256 checked). `/opt/data/attachments/openviking-hub-trial.md` carried a "Scope update —
2026-10-08" paragraph; it has been merged into the repo doc, and the two copies are now identical.

## Unverified

- The `/opt/data` skill and report files were not opened by me; their paths come from cognee.
- The checklist file exists per Hermes's first read, but Hermes later said it found no separate project-owned checklist file.
  Not reconciled.

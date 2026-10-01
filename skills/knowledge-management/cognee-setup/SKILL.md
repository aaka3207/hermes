---
name: cognee-setup
description: "How the self-hosted cognee memory backend is built and configured, its known traps, and how to diagnose slow, wrong or missing recall. Load before explaining or troubleshooting tier-3 memory."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [knowledge-management, memory, cognee, operations]
---

# Cognee setup

`SOUL.md` covers how to *use* tier-3 memory (`cognee_recall` / `cognee_remember`,
search modes, what to store). This skill covers how that store is *built*: load
it when the user asks how memory works, when recall is slow or wrong, or before
touching the cognee API directly.

State as of 2026-10-01. The full record lives in the repo (see the last section).
Where this skill and the repo disagree, the repo wins; where the repo and the
live system disagree, the live system wins. Say which one you checked.

## Architecture

- **Backend:** cognee 1.6.1, self-hosted on the Coolify host as service
  `lndyf8z46p75oh524khm5z19` (containers `cognee-backend`, `cognee-postgres`,
  `cognee-mcp`, `cognee-ui`). Postgres with pgvector holds the vectors; Kuzu
  (embedded) holds the graph; OpenRouter supplies the LLM and embeddings.
- **Hermes** talks to the backend over HTTP from inside its own process, through
  the `cognee` memory provider plugin. It does **not** go through MCP or
  metamcp. If `cognee-mcp` is down, Hermes memory still works.
- **Claude Desktop** reaches the same backend through metamcp -> `cognee-mcp`.
  Both agents read and write the **same dataset, `shared`**. Anything you store
  there, Desktop sees, and the reverse.
- **The plugin** is a fork, `aaka3207/cognee-integrations`, pinned by commit
  sha in the Hermes image. The fork adds the `search_type`, `session_writes`,
  `write_metadata` and `context_only` behaviour. Do not `pip install` the
  upstream package over it.
- The `dinefile` profile is separate: it uses Cognee Cloud, not this backend.

## Live configuration

Your config is `/opt/data/cognee.json`:

| key | value | why |
|---|---|---|
| `service_url` | `http://cognee-backend:8000` | the internal address |
| `dataset` | `shared` | the store shared with Desktop |
| `search_type` | `CHUNKS` | recall returns stored text, not LLM prose |
| `auto_route` | `true` | leave it: `false` pins `GRAPH_COMPLETION`, the worst mode measured |
| `session_writes` | `false` | no per-turn session-cache writes |
| `improve_on_end` | `false` | no session-end promotion into the dataset |
| `write_metadata` | `true` | each write carries `created_at`, `created_by`, session, `notion_page_id` |

The backend runs with `CACHING=false` (since 2026-09-30). Cognee keeps no
session Q&A history, so the injected `## Cognee Memory` block has no
"Previous conversation" layer any more. Old session rows expire by 2026-10-07.

## What reaches your prompt

1. **The injected `## Cognee Memory` block.** The plugin's prefetch lane runs on
   every turn with `HYBRID_COMPLETION`, `scope=["graph"]`, `only_context=True`.
   That is upstream design; it ignores `search_type` and there is no config key
   to turn it off. No LLM writes an answer, but part of the context is summaries
   an LLM wrote when each memory was stored. Treat it as a hint.
2. **`cognee_recall` results.** `CHUNKS` by default: verbatim stored text plus
   metadata. This is the source for anything you act on.

## Traps

- **`COGNEE_BASE_URL` in the container env points at the dinefile cloud
  tenant, not this backend.** Never aim an API call with it. Read
  `service_url` and `api_key` from `/opt/data/cognee.json`. A bulk delete was
  once aimed at the wrong tenant this way.
- Send the key as `X-Api-Key`, not `Authorization: Bearer`. Never print it.
- **The graph splits aliases.** The extractor never sees existing entities, so
  `ameer` and `ameer akashe` can be separate nodes with nothing linking them.
  A graph answer that misses half the facts about a person may be this.
- `forget` and dataset deletes are permanent. Production writes and deletes
  go to the user first, with the exact command.
- Records are dated assertions, never edited. A newer record supersedes an
  older one by saying so; both stay in the store. When two records disagree,
  prefer the newer `created_at` and the Notion page it points to.

## Diagnosing

- **Recall is slow (over 10s).** `CHUNKS` takes about 0.5s and graph modes 1-4s.
  If a completion-type recall stalls for tens of seconds, check the backend
  still has `CACHING=false`; with caching on, a hidden per-turn LLM call
  (`AUTO_FEEDBACK`) runs with no timeout.
- **Recall returns something wrong or stale.** Check which mode produced it.
  Completion modes (`GRAPH_COMPLETION`, `RAG_COMPLETION`) paraphrase names,
  IDs and dates. Re-run with `CHUNKS` and read the stored text and its
  `created_at`.
- **A record you just stored is missing.** A write runs the full pipeline
  (chunk, extract, embed) and takes about 10s. Re-query by a distinctive exact
  term with `CHUNKS_LEXICAL` before concluding it failed.
- **The backend is down.** Every cognee tool fails, Hermes and Desktop alike.
  Report it; restarting the Coolify service is the user's call.

## The full record: the hermes repo

The repo is `aaka3207/hermes` on GitHub (branch `main`). Read a file with
`gh api repos/aaka3207/hermes/contents/<path> -H "Accept: application/vnd.github.raw"`.

- `docs/cognee-operations.md`: what is running and how to operate it.
  §1 architecture, §5 where configuration lives (and the `api_key` /
  `COGNEE_BASE_URL` traps), §7 problems already solved, §9 runbook, rollback
  and bulk retraction, §10 current live state with dated changes,
  §11 open evaluation questions.
- `docs/cognee-memory-design.md`: why it is built this way. §0 is the summary;
  §17 covers session caching, `context_only` recall, entity splitting and
  graph versus `CHUNKS`.
- `deploy/cognee-selfhost.compose.yaml`: the backend compose, source of truth
  for the Coolify service (applied by hand, not auto-deployed).
- `scripts/cognee/`: verification and maintenance scripts, each documented in
  its own docstring.

These docs are long. Read §0 or the section you need, not the whole file.

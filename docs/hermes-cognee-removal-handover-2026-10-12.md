# Removing cognee from Hermes runtime (handover, 2026-10-12)

Scope: what Hermes **loads and runs** on the server (`/opt/data`, container `hermes-tgg4k0sc8wgocck08cc4s4cg-*`). Out of scope for now: the `dinefile` profile
(`provider: cognee`), the cognee plugin entry, `.env` keys, `cognee.json`, `.cognee*` dirs, the repo, and the self-hosted backend.

**Status: executed 2026-10-12 by Hermes, as removal-only, which differs from this draft in places.** Verified read-only afterwards:

| Item | Result |
|---|---|
| Four crons (`31fbd155fb23`, `e883c9c74d0c`, `c261b1135f7f`, `7f0d9b20fcfb`) | Paused (not removed) |
| `SOUL.md` | 145 -> 80 lines, 0 cognee mentions. Hermes wrote its own memory section (tier 3 = "shared OpenViking knowledge base", `viking_search` / `viking_read`, points at the `openviking-*` skills) rather than using section 2 below. Two safeguards were dropped: "confirm identifiers before acting" and "a tier-3 record that names a Notion page is a pointer". No pre-change backup of SOUL.md exists; the newest older copy is `SOUL.md.bak-cognee-setup-20261001-103602`. |
| Skills | `cognee-setup/` and `knowledge-operations/references/session-distillation.md` **deleted** (ledger: `archived: false`); cognee bullets removed from `knowledge-operations`, `notion-knowledge-capture`, `notion-cli-operations`. The generic guidance was not diffed. |
| `memories/MEMORY.md` | **Not changed.** Lines 3 and 9 still say cognee / "temporarily unavailable". |
| Out of scope, untouched | `dinefile` profile, plugin entry, `.env` keys, `cognee.json`, `.cognee*`, scripts, the repo, the backend |

Three paused cron prompts still mention `lcm_inspect` / `lcm.db`. The original draft follows for reference.

Original status line: SOUL.md and the skills are Hermes-maintained, so this was text to hand over; crons and config are production writes (gated).

State read 2026-10-12 (sha256 of the files this doc was written against, so you can tell if they drifted):

| File | sha256 |
|---|---|
| `SOUL.md` | `c43465e4dcb004b5890a8060532578809d34b18b2b9aace325b3edbcb64407e8` |
| `memories/MEMORY.md` | `5cad245c077f281a276789a420b380a86ba28923b100c85108ad538787aa1b5f` |
| `cron/jobs.json` | `ba311b92f1611cbdeae938d4fb50480f15715aed2f9e3c50d32796175117c135` |

Facts used below, read from the installed plugin (`/opt/data/plugins/openviking`, v2.1.1):

- Tools: `viking_search(query, mode auto|fast|deep, scope, limit)`, `viking_read(uri|uris, level abstract|overview|full)`, `viking_browse(action, path)`,
  `viking_remember(content)`, `viking_forget(uri)`, `viking_add_resource(...)`.
- `viking_remember` takes **only `content`**. There is no `metadata` argument, so SOUL.md's `metadata.notion_page_id` rule cannot survive; the page ID goes in the text.
- `viking_remember` means "submitted for extraction"; OpenViking may add, merge or skip. It does not return a stable URI.
- Auto-injected context is headed `## OpenViking Context`. The plugin's own system block already tells Hermes to search before asking the user to repeat context.
- Native `MEMORY.md` / `USER.md` writes are mirrored into OpenViking by `native_memory_mirror.py`.

## 1. Crons (four enabled jobs still call cognee daily)

Each run loads the full `knowledge-operations` skill into the prompt: 46k, 78k, 75k and 142k characters respectively, every day.

| Job id | Name | Schedule | Script |
|---|---|---|---|
| `31fbd155fb23` | Cognee session distill | `0 3 * * *` | `cognee_session_distill_scan.py` |
| `e883c9c74d0c` | Cognee to Notion filing | every 1440m | none |
| `c261b1135f7f` | Cognee redundancy check | `45 9 * * *` | none |
| `7f0d9b20fcfb` | Memory steward | `30 10 * * *` | none |

Pause, not remove (reversible, keeps definitions and history):

```bash
C=hermes-tgg4k0sc8wgocck08cc4s4cg-193931467615   # re-resolve with docker ps if it changed
for j in 31fbd155fb23 e883c9c74d0c c261b1135f7f 7f0d9b20fcfb; do docker exec $C hermes cron pause $j; done
docker exec $C hermes cron list
```

Verify: all four show paused, the other five (HA weight sync, Hevy report, Incued reminder, finance close, pizza reminder) are untouched.
Undo: `hermes cron resume <id>`. Removal comes later, once replacements exist (or are decided against).

What the pause drops, so the decision is explicit:
- Session distill: OpenViking already extracts from sessions on commit, so this has no direct replacement need.
- Cognee to Notion filing / redundancy check: operate on the cognee dataset, which is no longer the active store.
- Memory steward: its hygiene role has no OpenViking equivalent yet. Decide separately whether to write one.

## 2. SOUL.md: replace "Durable memory: four tiers" (lines 12-123)

Lines 1-11 and 125-145 stay as they are (the Career section already points at OpenViking). Proposed replacement for lines 12-123:

````markdown
## Durable memory: four tiers

**Anything you will act on must be confirmed from the stored record.** Search
results from `viking_search` are ranked excerpts and may be summaries OpenViking
wrote when it extracted the memory, so they can paraphrase names, IDs and dates.
Before acting on an identifier, URL, date or name, open the record with
`viking_read` at `full` level, or fetch the Notion page it points to.

The `## OpenViking Context` block you are given automatically is retrieved for
the turn and is a hint about what to go and check, never a source.

Two of the four tiers are Hermes-only. Claude Desktop cannot see this prompt,
your memory files, or your session history, so anything another agent may need
has to reach tier 3 or tier 4 -- otherwise it does not exist outside this
process.

| Tier | Where | Holds | Visible to |
|---|---|---|---|
| 1 | this prompt; `memories/USER.md`; `memories/MEMORY.md` | how you should behave; who the user is and how they want things done (USER.md); facts about your environment (MEMORY.md) | Hermes only -- except USER.md and MEMORY.md entries, which the plugin also mirrors into tier 3 |
| 2 | Hermes-LCM session history | what was actually said, and when | Hermes only |
| 3 | shared store (OpenViking: `viking_search` / `viking_read` / `viking_remember`) | durable facts about the user | Hermes and Claude Desktop |
| 4 | Notion hubs | the current state of a project, person, or plan | Hermes and Claude Desktop |

### Reading

Search tier 3 before asking the user to repeat context. Beyond that, route by
what you are actually asking for:

- A standing fact or preference about the user -> tier 3, `viking_search`.
- What was said or decided in an earlier conversation -> tier 2, Hermes-LCM.
- The current state of a project, person, or plan -> tier 4, the mapped hub.
- How you should behave -> already in this prompt; do not go looking for it.

Prefer one or two focused searches, then `viking_read` the strongest URIs. Use
`mode: "fast"` for a simple lookup and `"deep"` for a question that needs
reasoning across several records. If repeated searches return the same evidence,
stop and answer from it, and say what is uncertain.

A tier-3 record that names a Notion page is a pointer, not an answer. Fetch the
page: the record says where to look and may itself be out of date.

### Writing

- Who the user is, a lasting preference, or how they expect you to behave ->
  the memory tool, target `user` (USER.md). It is loaded on every turn and
  mirrored into tier 3, so Claude Desktop sees it too. It is small: keep it to
  what should shape every conversation.
- A fact about your own environment -- a tool quirk, config gotcha, path or
  endpoint -> the memory tool, target `memory` (MEMORY.md).
- A small durable fact about the user -> `viking_remember`, in the same turn you
  learn it. Do not wait for the conversation to end, and do not treat having said
  it here as having stored it. This transcript is not storage. Success means the
  text was submitted for extraction, not that a specific memory file now exists;
  allow time before reading it back, and confirm with `viking_search`.
- Something substantial -> a page in the right Notion hub, plus one
  `viking_remember` pointer that names the page and states its page ID in the
  text, so it can be found without already knowing which hub it went to.
- Deleting a memory -> `viking_forget` with the exact `viking://` URI, only when
  the user asks for that specific memory to go.

### What belongs in tier 3

Store only durable, user-centric facts that are useful across conversations and
safe to share. Each stored fact must include the date it became true or was
observed; include a validity period or expiry for time-bound facts when known.
Distinguish the fact date from the date it was recorded, and do not invent
dates. When storing a memory that came out of a Hermes conversation, obtain and
confirm the originating Hermes session ID with the Hermes-LCM tool and include
that verified ID in the text, so the source conversation can be found and
audited. Never infer or invent a session ID; if LCM cannot confirm one, say so in
the record rather than storing it as though provenance were verified.

Appropriate durable facts include:
- Stable facts about the user: identity, role, environment, and how their setup works.
- Lasting preferences: tools, workflows, and styles they favor or avoid.
- Standing user instructions: how the user wants things done.
- Real commitments and dated events, with a validity period or expiry when time-bound.

Do not store transient task state, debugging details, tool output, logs, errors,
one-off chatter, or anything that only makes sense with the current conversation
or project open. Keep sensitive information about other people proportionate to
a real need. Before adding a fact, search for an existing memory that should be
updated or superseded rather than creating a contradiction.
````

Changes against the current text, so nothing is dropped silently:
- Removed: the whole "How the shared store searches" section (`CHUNKS`, `CHUNKS_LEXICAL`, `context_only`) and the `cognee-setup` skill pointer. They describe a cognee-only API.
- Removed: `metadata: {"notion_page_id": ...}`, its refusal behaviour, and "a pointer written since 2026-09-29 carries the page ID in its result's metadata". `viking_remember` has no such argument.
- Removed: "Call `cognee_recall` at the start of every conversation." The plugin already prefetches at session start; the replacement says search before asking the user to repeat context.
- Changed: tier 1 now says MEMORY.md is also mirrored (per `native_memory_mirror.py`). Check this against current behaviour before trusting it: I read the module docstring, not a live mirror result.
- Kept unchanged: the tier-3 content rules (dates, provenance, no transient state).
- Dropped the "1,375 characters" USER.md limit; I could not verify that figure from here.

Open risk: Claude Desktop shares the owner root with Hermes, so the "visible to Hermes and Claude Desktop" claim for tier 3 holds, but Hermes junk also lands there (see `docs/openviking-peer-contamination-2026-10-10.md`). The decision there was to accept the leak.

## 3. MEMORY.md (two lines)

Line 3 (tier note), replace with:

> Tier boundary for this file: it is injected into every Hermes turn and is invisible to Claude Desktop and other agents. Put facts about the Hermes environment here (tool quirks, config, paths). User preferences and expectations go in USER.md; other durable user facts go to OpenViking via `viking_remember`. Revised 2026-10-12.

Line 9 (stale "Cognee is temporarily unavailable; OpenViking is being tried"), replace with:

> OpenViking is the active shared memory (provider `openviking`, owner key, since 2026-10-08). `viking_remember` submits for extraction and may take time to appear; an immediate `viking_search` can miss it, so allow time before read-back verification.

## 4. Skills (not drafted yet)

Cognee lines I found in live skills, for the next pass:

| Skill | Lines | Treatment |
|---|---|---|
| `productivity/notion-knowledge-capture` | 25, 52, 81 | Three one-line swaps `cognee_remember` -> `viking_remember`. Small. |
| `productivity/notion-cli-operations` | 132 | One mention inside a verification step; read the line before editing. |
| `knowledge-management/knowledge-operations` | 45, 49, 52-55, 59, 65-81 | Mostly a cognee operating manual (transport choice, 400-char records, CHUNKS comparison, "Cognee retirement"). Needs a rewrite or a trim, not a find-and-replace. Also loaded by all four crons above, which is part of why they are 46-142k characters. |
| `knowledge-management/knowledge-operations/references/session-distillation.md` | 9, 16, 17, 30-32, 37 | Belongs to the paused distill cron and the `cognee_session_distill_scan.py` script. Archive with the cron. |
| `knowledge-management/cognee-setup` (+ `references/maintenance.md`) | whole skill | Archive to `skills/.archive/`, as was done with `mnemosyne-memory-hygiene`. Do it last: the dinefile profile may still want it. |

`scripts/cognee_dataset_api.py` and `scripts/cognee_session_distill_scan.py` stay in place until the dinefile decision.

## 5. Verification after the SOUL.md swap

1. Start a Hermes session; ask for something only OpenViking holds. Expect it to call `viking_search`, not `cognee_recall`.
2. Ask it to remember a throwaway fact; expect `viking_remember`, with the date in the text and no `metadata` argument.
3. `grep -c -i cognee /opt/data/SOUL.md` returns 0.
4. `hermes cron list` shows the four jobs paused; tomorrow's `cron/output/` has no new files for those ids.
5. `agent.log` shows no `cognee_recall` / `cognee_remember` tool calls after the change.

## 6. Rollback

- Crons: `hermes cron resume <id>`.
- SOUL.md and MEMORY.md: restore from `SOUL.md.bak-cognee-setup-20261001-103602` (older, so prefer a fresh `cp` of the live file taken just before the swap) and `memories/MEMORY.md.bak-cognee-setup-20261001-103602`.

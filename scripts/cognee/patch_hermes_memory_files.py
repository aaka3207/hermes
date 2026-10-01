#!/usr/bin/env python3
"""Bring Hermes's SOUL.md, USER.md and MEMORY.md in line with the cognee setup.

    docker cp scripts/cognee/patch_hermes_memory_files.py <hermes>:/tmp/
    docker exec -u hermes <hermes> python3 /tmp/patch_hermes_memory_files.py --commit

Dry run by default: prints each edit and whether it applies.

Where things go follows Hermes's own routing (agent/background_review.py and the
"which file does what" docs): USER.md is who the user is, their preferences
and how they expect Hermes to behave; MEMORY.md is facts about Hermes's
environment (tool quirks, config gotchas, paths). Anything operational Hermes
needs on every turn stays in these core files, not in skills.

SOUL.md (memory section only; the hub routing is untouched):

* The ``Previous conversation:`` paragraph. That layer came from cognee's
  session Q&A cache, off since the backend got ``CACHING=false`` on
  2026-09-30. The replacement keeps the rule (the block is a hint; confirm
  identifiers before acting) with the reason that still holds.
* "the graph-and-LLM path": the lane runs with ``only_context=True``, so no LLM
  answers at recall time. Also points at the ``cognee-setup`` skill.
* The tier table's tier-1 row and the first writing rule. SOUL.md sent "how you
  should behave" to MEMORY.md, the opposite of Hermes's built-in routing. Both
  now say: the user's preferences and expectations -> USER.md, which the plugin
  copies into tier 3 (``memory_write_targets: ["user"]``); environment facts ->
  MEMORY.md, Hermes-only.

USER.md / MEMORY.md, edited per entry (``\\n§\\n``-delimited, as the memory tool
writes them):

* USER.md: drops one empty entry and the Health HQ URL (a path, and already in
  SOUL.md's hub table); gains the career-drafting rule, shortened to fit.
* MEMORY.md: loses the career rule (a user expectation, so it belongs in
  USER.md); the tier-boundary note is reworded to match.

Each edit matches exact text and must match exactly once; an edit whose result
is already present is a no-op, so a second run changes nothing. Anything else
means the file drifted: the script stops before writing anything. The memory
files must stay under Hermes's limits (1375 / 2200 characters). Writes
atomically, keeps the file mode, and leaves a timestamped backup beside each
changed file.
"""
import argparse
import os
import sys
import tempfile
import time

HOME = "/opt/data"
DELIM = "\n§\n"
LIMITS = {"memories/USER.md": 1375, "memories/MEMORY.md": 2200}

SOUL_EDITS = [
    ("previous-conversation paragraph",
     """The `## Cognee Memory` block you are given automatically is not the same thing
and is not all stored text. Its `Previous conversation:` layer is a set of
answers written by an earlier turn: it can be stale, irrelevant to the current
question, and self-contradictory -- blocks have carried two different creation
dates for the same page. Treat that layer as a hint about what to go and check,
never as a source. Before acting on any identifier, URL, date or name, confirm it
with `cognee_recall` or by fetching the Notion page.""",
     """The `## Cognee Memory` block you are given automatically is not the same thing.
It is graph context retrieved for this turn, and part of it is summaries an LLM
wrote when each memory was stored, so it can paraphrase names, IDs and dates.
Treat it as a hint about what to go and check, never as a source. Before acting
on any identifier, URL, date or name, confirm it with `cognee_recall` or by
fetching the Notion page."""),
    ("tier-1 row",
     "| 1 | this prompt; `memories/MEMORY.md`; `memories/USER.md` | how you should behave; Hermes-only operational facts | Hermes only |",
     "| 1 | this prompt; `memories/USER.md`; `memories/MEMORY.md` | how you should behave; who the user is and how they want things done (USER.md); facts about your environment (MEMORY.md) | Hermes only -- except USER.md entries, which are also copied into tier 3 |"),
    ("graph-and-LLM wording",
     """The injected `## Cognee Memory` block is the graph-and-LLM path run
automatically on every prompt, which is why it is a hint and not a record.""",
     """The injected `## Cognee Memory` block is the graph path run automatically on
every prompt, which is why it is a hint and not a record. For how the store is
built, its known traps, and diagnosing slow or wrong recall, load the
`cognee-setup` skill."""),
    ("writing: USER.md vs MEMORY.md",
     "- About how you should behave -> `memories/MEMORY.md`.\n",
     """- Who the user is, a lasting preference, or how they expect you to behave ->
  the memory tool, target `user` (USER.md). It is loaded on every turn and
  copied into tier 3, so Claude Desktop sees it too. It is small (1,375
  characters): keep it to what should shape every conversation.
- A fact about your own environment -- a tool quirk, config gotcha, path or
  endpoint -> the memory tool, target `memory` (MEMORY.md). Hermes-only.
"""),
]

CAREER_OLD = ("For career-related requests, block drafting until the live Career Strategy "
              "Hub, current-month Changelog, and the specific relevant linked career record "
              "have all been read in that order. Verify the actual person/message and "
              "requested outcome; never infer the coach or recipient. On correction or "
              "“pull it again,” reopen the exact record first.")
CAREER_NEW = ("Career requests: before drafting, read the Career Strategy Hub, this month's "
              "Changelog, then the linked record, in that order. Never infer the coach or "
              "recipient; on “pull it again,” reopen the exact record.")
HEALTH_HQ = ("Official health hub: Health HQ: "
             "https://www.notion.so/Health-HQ-340ebcff7b9b81ab9de0f4fa99d796d0.")
BOUNDARY_OLD = ("Tier boundary for this file: it is injected into every Hermes turn and is "
                "invisible to Claude Desktop and other agents. Put Hermes-specific operational "
                "workflows and procedures here or in the relevant skill; put durable user "
                "facts/preferences meant to be shared with other agents in Cognee. Recorded "
                "2026-09-27.")
BOUNDARY_NEW = ("Tier boundary for this file: it is injected into every Hermes turn and is "
                "invisible to Claude Desktop and other agents. Put facts about the Hermes "
                "environment here (tool quirks, config, paths). User preferences and "
                "expectations go in USER.md, which is copied into Cognee; other durable user "
                "facts go to Cognee directly. Recorded 2026-09-27, revised 2026-10-01.")


def edit_user(entries):
    report = []
    if "" in entries:
        entries = [e for e in entries if e != ""]
        report.append("  apply   drop empty entry")
    else:
        report.append("  no-op   drop empty entry (already applied)")
    if HEALTH_HQ in entries:
        entries = [e for e in entries if e != HEALTH_HQ]
        report.append("  apply   drop Health HQ URL")
    else:
        report.append("  no-op   drop Health HQ URL (already applied)")
    if CAREER_NEW in entries:
        report.append("  no-op   add career rule (already applied)")
    else:
        entries = entries + [CAREER_NEW]
        report.append("  apply   add career rule")
    return entries, report


def edit_memory(entries):
    report = []
    if CAREER_OLD in entries:
        entries = [e for e in entries if e != CAREER_OLD]
        report.append("  apply   drop career rule (moved to USER.md)")
    else:
        report.append("  no-op   drop career rule (already applied)")
    if BOUNDARY_OLD in entries:
        entries = [BOUNDARY_NEW if e == BOUNDARY_OLD else e for e in entries]
        report.append("  apply   reword tier-boundary note")
    elif BOUNDARY_NEW in entries:
        report.append("  no-op   reword tier-boundary note (already applied)")
    else:
        sys.exit("  DRIFT   tier-boundary note not found; nothing written")
    return entries, report


def plan_soul(text):
    report = []
    for label, old, new in SOUL_EDITS:
        n_old = text.count(old)
        if n_old == 1:
            text = text.replace(old, new)
            report.append(f"  apply   {label}")
        elif n_old == 0 and new in text:
            report.append(f"  no-op   {label} (already applied)")
        else:
            sys.exit(f"  DRIFT   {label}: old text found {n_old} times; nothing written")
    return text, report


def plan_entries(text, editor):
    trailing = "\n" if text.endswith("\n") else ""
    entries = text[: len(text) - len(trailing)].split(DELIM)
    entries, report = editor(entries)
    return DELIM.join(entries) + trailing, report


def write_atomic(path, text):
    mode = os.stat(path).st_mode & 0o777
    backup = f"{path}.bak-cognee-setup-{time.strftime('%Y%m%d-%H%M%S')}"
    with open(path, encoding="utf-8") as src, open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return backup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--commit", action="store_true", help="write the changes")
    args = ap.parse_args()

    planners = {
        "SOUL.md": plan_soul,
        "memories/USER.md": lambda t: plan_entries(t, edit_user),
        "memories/MEMORY.md": lambda t: plan_entries(t, edit_memory),
    }
    staged = []
    for rel, planner in planners.items():
        path = os.path.join(args.home, rel)
        with open(path, encoding="utf-8") as f:
            before = f.read()
        print(path)
        after, report = planner(before)
        print("\n".join(report))
        limit = LIMITS.get(rel)
        if limit and len(after.rstrip("\n")) > limit:
            sys.exit(f"  OVER LIMIT {rel}: {len(after)} > {limit} characters; nothing written")
        if after != before:
            staged.append((path, after, len(before), len(after)))

    if not staged:
        print("nothing to change")
        return
    for path, after, n_before, n_after in staged:
        if args.commit:
            backup = write_atomic(path, after)
            print(f"wrote {path} ({n_before} -> {n_after} chars), backup {backup}")
        else:
            print(f"would write {path} ({n_before} -> {n_after} chars); rerun with --commit")


if __name__ == "__main__":
    main()

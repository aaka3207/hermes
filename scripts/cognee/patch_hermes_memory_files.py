#!/usr/bin/env python3
"""Bring Hermes's SOUL.md and USER.md in line with the cognee setup, 2026-10-01.

    docker cp scripts/cognee/patch_hermes_memory_files.py <hermes>:/tmp/
    docker exec -u hermes <hermes> python3 /tmp/patch_hermes_memory_files.py --commit

Dry run by default: prints each edit and whether it applies.

SOUL.md, two edits in the durable-memory section:

* The paragraph about the injected block's ``Previous conversation:`` layer.
  That layer came from cognee's session Q&A cache, which has been off since the
  backend got ``CACHING=false`` on 2026-09-30, so the paragraph now warns about
  something that no longer appears. The replacement keeps the rule (the block
  is a hint; confirm identifiers before acting) and gives the reason that still
  holds: part of the block is summaries an LLM wrote at store time.
* "the graph-and-LLM path": the lane runs with ``only_context=True``, so no LLM
  answers at recall time. The replacement also points at the ``cognee-setup``
  skill, which is how Hermes finds the setup knowledge.

USER.md: removes one empty entry (two ``§`` separators in a row).

Each edit matches exact text and must match exactly once. An edit whose new text
is already present is a no-op, so a second run changes nothing. If the old text
is missing and the new text is absent too, the file has drifted: the script
stops before writing anything. Writes atomically, keeps the file mode, and
leaves a timestamped backup beside each changed file.
"""
import argparse
import os
import sys
import tempfile
import time

HOME = "/opt/data"

SOUL_OLD_1 = """The `## Cognee Memory` block you are given automatically is not the same thing
and is not all stored text. Its `Previous conversation:` layer is a set of
answers written by an earlier turn: it can be stale, irrelevant to the current
question, and self-contradictory -- blocks have carried two different creation
dates for the same page. Treat that layer as a hint about what to go and check,
never as a source. Before acting on any identifier, URL, date or name, confirm it
with `cognee_recall` or by fetching the Notion page."""

SOUL_NEW_1 = """The `## Cognee Memory` block you are given automatically is not the same thing.
It is graph context retrieved for this turn, and part of it is summaries an LLM
wrote when each memory was stored, so it can paraphrase names, IDs and dates.
Treat it as a hint about what to go and check, never as a source. Before acting
on any identifier, URL, date or name, confirm it with `cognee_recall` or by
fetching the Notion page."""

SOUL_OLD_2 = """The injected `## Cognee Memory` block is the graph-and-LLM path run
automatically on every prompt, which is why it is a hint and not a record."""

SOUL_NEW_2 = """The injected `## Cognee Memory` block is the graph path run automatically on
every prompt, which is why it is a hint and not a record. For how the store is
built, its known traps, and diagnosing slow or wrong recall, load the
`cognee-setup` skill."""

USER_OLD = "\n§\n\n§\n"
USER_NEW = "\n§\n"

EDITS = {
    "SOUL.md": [("previous-conversation paragraph", SOUL_OLD_1, SOUL_NEW_1),
                ("graph-and-LLM wording", SOUL_OLD_2, SOUL_NEW_2)],
    "memories/USER.md": [("empty entry", USER_OLD, USER_NEW)],
}


def plan(text, edits):
    """Return (new_text, report lines); raise SystemExit on drift."""
    report = []
    for label, old, new in edits:
        n_old = text.count(old)
        if n_old == 1:
            text = text.replace(old, new)
            report.append(f"  apply   {label}")
        elif n_old == 0 and new in text:
            report.append(f"  no-op   {label} (already applied)")
        else:
            sys.exit(f"  DRIFT   {label}: old text found {n_old} times; nothing written")
    return text, report


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

    staged = []
    for rel, edits in EDITS.items():
        path = os.path.join(args.home, rel)
        with open(path, encoding="utf-8") as f:
            before = f.read()
        print(path)
        after, report = plan(before, edits)
        print("\n".join(report))
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

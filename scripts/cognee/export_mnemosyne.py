#!/usr/bin/env python3
"""Export the Mnemosyne SQLite store to JSONL for seeding into Cognee.

READ-ONLY, always. The live store is the rollback path for the entire
evaluation: if Cognee does not work out, flipping memory.provider back is only
safe because this file was never touched. The connection is opened through a
`file:...?mode=ro` URI so a write is refused by SQLite rather than merely
avoided by us.

WHICH STORE: the live one is the Hermes gateway's, `/data/mnemosyne.db` inside
the container (reported by mnemosyne_stats). It is NOT
`~/.hermes/mnemosyne/data/mnemosyne.db` on the laptop -- that file is a stale
Aug 2026 artifact whose three source tables are empty, so exporting it would
silently produce an empty seed. Verify counts against mnemosyne_stats
immediately before exporting, and pass the server path explicitly.

Verified row counts on the live store (2026-09-20): memories 3, episodic_memory
252, working_memory 1216 -- about 312 KB of prose in total.

Usage:
  python3 scripts/cognee/export_mnemosyne.py /path/to/mnemosyne.db > seed.jsonl

A zero-record export exits non-zero -- that is almost always the stale,
empty laptop copy at ~/.hermes/mnemosyne/data/mnemosyne.db rather than the
live store, and that failure must not be silent (a downstream seed step
would otherwise happily consume an empty seed.jsonl). Pass --force to
downgrade that failure to a warning when an empty export is intentional.
"""
import json
import sqlite3
import sys
import urllib.parse

TABLES = ("memories", "episodic_memory", "working_memory")


def export(db_path):
    """Return a list of {source, text, created_at} dicts, newest table last.

    Diagnostics (per-table skip warnings, and the counts consumed by the CLI
    below) are written to stderr as they're discovered and also stashed on
    export.LAST_COUNTS / export.LAST_SKIPPED, so callers that only want the
    records don't have to parse stderr.
    """
    uri = "file:%s?mode=ro" % urllib.parse.quote(db_path)
    export.LAST_URI = uri
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    records = []
    counts = dict((table, 0) for table in TABLES)
    skipped = []
    try:
        for table in TABLES:
            try:
                cursor = con.execute(
                    "select content, created_at from %s order by rowid" % table)
            except sqlite3.OperationalError as e:
                # Could be "no such table" (benign, an older/newer schema
                # simply lacks it) or "no such column: content" (a real
                # schema mismatch that would otherwise drop rows silently).
                # Either way, the operator needs to see it.
                skipped.append((table, str(e)))
                print("warning: skipping table %r: %s" % (table, e),
                      file=sys.stderr)
                continue
            for row in cursor:
                text = (row["content"] or "").strip()
                if not text:
                    continue
                records.append({
                    "source": table,
                    "text": text,
                    "created_at": row["created_at"],
                })
                counts[table] += 1
    finally:
        con.close()
    export.LAST_COUNTS = counts
    export.LAST_SKIPPED = skipped
    return records


export.LAST_URI = ""
export.LAST_COUNTS = {}
export.LAST_SKIPPED = []


if __name__ == "__main__":
    argv = sys.argv[1:]
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]
    if len(argv) < 1:
        sys.exit("usage: export_mnemosyne.py <path-to-mnemosyne.db> [--force]")

    records = export(argv[0])
    for record in records:
        print(json.dumps(record, ensure_ascii=False))

    counts = export.LAST_COUNTS
    total = len(records)
    for table in TABLES:
        print("  %s: %d" % (table, counts.get(table, 0)), file=sys.stderr)
    print("total: %d" % total, file=sys.stderr)

    if total == 0:
        message = (
            "exported 0 records -- this is the signature of the stale, "
            "empty laptop copy (~/.hermes/mnemosyne/data/mnemosyne.db), not "
            "the live store; verify the path against mnemosyne_stats before "
            "trusting this output")
        if force:
            print("warning: %s" % message, file=sys.stderr)
        else:
            sys.exit("error: %s (pass --force to export anyway)" % message)

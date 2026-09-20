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
"""
import json
import sqlite3
import sys

TABLES = ("memories", "episodic_memory", "working_memory")


def export(db_path):
    """Return a list of {source, text, created_at} dicts, newest table last."""
    uri = "file:%s?mode=ro" % db_path
    export.LAST_URI = uri
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    records = []
    try:
        for table in TABLES:
            try:
                cursor = con.execute(
                    "select content, created_at from %s order by rowid" % table)
            except sqlite3.OperationalError:
                continue  # table absent in this schema version; not fatal
            for row in cursor:
                text = (row["content"] or "").strip()
                if not text:
                    continue
                records.append({
                    "source": table,
                    "text": text,
                    "created_at": row["created_at"],
                })
    finally:
        con.close()
    return records


export.LAST_URI = ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: export_mnemosyne.py <path-to-mnemosyne.db>")
    for record in export(sys.argv[1]):
        print(json.dumps(record, ensure_ascii=False))

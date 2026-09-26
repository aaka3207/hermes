#!/usr/bin/env python3
"""Back up every live record's raw text, keyed by data_id.

Runs inside the cognee-backend container, read-only:

    docker cp scripts/cognee/backup_corpus.py <backend>:/tmp/ && \
        docker exec <backend> python /tmp/backup_corpus.py

Makes any deletion reversible. ``/api/v1/forget`` removes the document and its
graph artifacts, and this file is what a re-ingest would be driven from.

``raw_data_location`` is a ``file://`` URI, not a path -- reading it as a path
silently reports every record as missing.

Two populations, two files. ``data`` holds the documents; ``SessionQAVector_text``
holds the raw ``User:``/``Assistant:`` turns that ``persist_session_qa`` writes,
which live only in that table and are invisible to the document backup. They are
the population that poisons completion-mode recall
(``docs/cognee-memory-design.md`` §4.4), so they are worth having on disk before
a wipe even though nothing would re-ingest them deliberately.
"""
import json
import os
from urllib.parse import unquote, urlparse

import psycopg2

OUT = "/tmp/corpus_backup.json"
OUT_SESSIONS = "/tmp/session_qa_backup.json"


def to_path(loc):
    if not loc:
        return None
    return unquote(urlparse(loc).path) if loc.startswith("file://") else loc


def main():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], user=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"], dbname=os.environ["DB_NAME"],
    )
    cur = conn.cursor()
    cur.execute(
        "select id, name, raw_data_location, dataset_id, node_set, token_count,"
        " data_size, created_at from data order by created_at"
    )

    out, missing = [], 0
    for rid, name, loc, ds, nodeset, tok, size, created in cur.fetchall():
        path = to_path(loc)
        text = None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            missing += 1
        out.append({
            "id": str(rid), "name": name, "dataset_id": str(ds),
            "node_set": nodeset, "token_count": tok, "data_size": size,
            "created_at": created.isoformat(), "raw_data_location": loc,
            "text": text,
        })

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)

    print("records:", len(out), "| missing raw:", missing)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")

    dump_session_qa(cur)


def dump_session_qa(cur):
    """Dump the session Q&A vector rows, if the table exists.

    Guarded rather than assumed: the table is created by ``persist_session_qa``,
    so a store that has never run ``improve()`` will not have it, and a missing
    table is not a failed backup.
    """
    cur.execute("select to_regclass('public.\"SessionQAVector_text\"')")
    if cur.fetchone()[0] is None:
        print("SessionQAVector_text: absent, nothing to back up")
        return

    cur.execute('select id, payload from "SessionQAVector_text"')
    rows = [{"id": str(rid), "payload": payload} for rid, payload in cur.fetchall()]
    with open(OUT_SESSIONS, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1, default=str)

    print("session Q&A rows:", len(rows))
    print("wrote", OUT_SESSIONS, os.path.getsize(OUT_SESSIONS), "bytes")


main()

#!/usr/bin/env python3
"""Back up every live record's raw text, keyed by data_id.

Runs inside the cognee-backend container, read-only:

    docker cp scripts/cognee/backup_corpus.py <backend>:/tmp/ && \
        docker exec <backend> python /tmp/backup_corpus.py

Makes any deletion reversible. ``/api/v1/forget`` removes the document and its
graph artifacts, and this file is what a re-ingest would be driven from.

``raw_data_location`` is a ``file://`` URI, not a path -- reading it as a path
silently reports every record as missing.
"""
import json
import os
from urllib.parse import unquote, urlparse

import psycopg2

OUT = "/tmp/corpus_backup.json"


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


main()

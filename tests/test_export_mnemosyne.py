#!/usr/bin/env python3
"""Tests for scripts/cognee/export_mnemosyne.py.

Builds a synthetic SQLite store with the real Mnemosyne table shapes, so no
access to the live 95 MB store is needed and the tests are safe to run anywhere.

    python3 tests/test_export_mnemosyne.py

The read-only guarantee is the load-bearing case: the live store is the
rollback path for the whole evaluation, so the exporter must be incapable of
writing to it.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from export_mnemosyne import export

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def build_store(path):
    con = sqlite3.connect(path)
    con.execute("create table memories (id integer primary key, content text, created_at text)")
    con.execute("create table episodic_memory (id integer primary key, content text, created_at text)")
    con.execute("create table working_memory (id integer primary key, content text, created_at text)")
    con.execute("insert into memories (content, created_at) values (?,?)",
                ("Ameer prefers plain markdown deliverables.", "2026-01-01T00:00:00"))
    con.execute("insert into episodic_memory (content, created_at) values (?,?)",
                ("Deployed the RTK rewrite on 2026-09-19.", "2026-09-19T10:00:00"))
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                ("  ", "2026-09-19T11:00:00"))          # blank -> must be skipped
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                (None, "2026-09-19T12:00:00"))           # null -> must be skipped
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                ("Uses Coolify on a single home server.", "2026-09-19T13:00:00"))
    con.commit()
    con.close()


tmp = tempfile.mkdtemp(prefix="mnemo-export-")
db = os.path.join(tmp, "mnemosyne.db")
build_store(db)

print("\n1/5 exports every non-empty row")
rows = export(db)
check("3 rows exported", len(rows) == 3, "got %d" % len(rows))

print("\n2/5 blank and null content are skipped")
texts = [r["text"] for r in rows]
check("no blank text", all(t and t.strip() for t in texts), repr(texts))

print("\n3/5 every row is tagged with its source table")
sources = sorted({r["source"] for r in rows})
check("sources tagged", sources == ["episodic_memory", "memories", "working_memory"],
      repr(sources))

print("\n4/5 timestamps are preserved")
check("created_at preserved",
      any(r["created_at"] == "2026-01-01T00:00:00" for r in rows),
      repr([r["created_at"] for r in rows]))

print("\n5/5 the source database is opened READ-ONLY")
before = os.path.getmtime(db)
export(db)
after = os.path.getmtime(db)
check("mtime unchanged", before == after, "%s -> %s" % (before, after))
uri_used = getattr(export, "LAST_URI", "")
check("opened via mode=ro URI", "mode=ro" in uri_used, repr(uri_used))

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all mnemosyne export tests passed")

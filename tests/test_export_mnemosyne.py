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
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from export_mnemosyne import export

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee",
                       "export_mnemosyne.py")

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

print("\n1/9 exports every non-empty row")
rows = export(db)
check("3 rows exported", len(rows) == 3, "got %d" % len(rows))

print("\n2/9 blank and null content are skipped")
texts = [r["text"] for r in rows]
check("no blank text", all(t and t.strip() for t in texts), repr(texts))

print("\n3/9 every row is tagged with its source table")
sources = sorted({r["source"] for r in rows})
check("sources tagged", sources == ["episodic_memory", "memories", "working_memory"],
      repr(sources))

print("\n4/9 timestamps are preserved")
check("created_at preserved",
      any(r["created_at"] == "2026-01-01T00:00:00" for r in rows),
      repr([r["created_at"] for r in rows]))

print("\n5/9 the source database is opened READ-ONLY")
before = os.path.getmtime(db)
export(db)
after = os.path.getmtime(db)
check("mtime unchanged", before == after, "%s -> %s" % (before, after))
uri_used = getattr(export, "LAST_URI", "")
check("opened via mode=ro URI", "mode=ro" in uri_used, repr(uri_used))
# mtime and the URI string are both satisfied by a connection that merely
# happens not to write. Prove the connection SQLite actually hands back for
# this URI refuses a write -- swap the URI for a plain path and this fails.
write_refused = ""
try:
    probe = sqlite3.connect(uri_used, uri=True)
    try:
        probe.execute("insert into memories (content, created_at) values (?,?)",
                      ("a write that must never land", "2026-09-20T00:00:00"))
        probe.commit()
    finally:
        probe.close()
except sqlite3.OperationalError as exc:
    write_refused = str(exc)
check("a write through the exporter's own URI is refused by SQLite",
      "readonly" in write_refused, repr(write_refused) or "(the write SUCCEEDED)")
check("the store is still unmodified after the write attempt",
      os.path.getmtime(db) == before, "%s -> %s" % (before, os.path.getmtime(db)))

def build_empty_store(path):
    con = sqlite3.connect(path)
    con.execute("create table memories (id integer primary key, content text, created_at text)")
    con.execute("create table episodic_memory (id integer primary key, content text, created_at text)")
    con.execute("create table working_memory (id integer primary key, content text, created_at text)")
    con.commit()
    con.close()


empty_tmp = tempfile.mkdtemp(prefix="mnemo-export-empty-")
empty_db = os.path.join(empty_tmp, "mnemosyne.db")
build_empty_store(empty_db)

print("\n6/9 a zero-record export exits non-zero")
result = subprocess.run([sys.executable, SCRIPT, empty_db],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
check("non-zero exit on empty export", result.returncode != 0,
      "returncode=%d stderr=%r" % (result.returncode, result.stderr))
check("no stdout records on empty export", result.stdout.strip() == "",
      repr(result.stdout))

print("\n7/9 --force downgrades the empty-export failure to a warning")
result = subprocess.run([sys.executable, SCRIPT, empty_db, "--force"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
check("zero exit with --force", result.returncode == 0,
      "returncode=%d stderr=%r" % (result.returncode, result.stderr))
check("warning still printed to stderr with --force", "warning:" in result.stderr,
      repr(result.stderr))


def build_partial_store(path):
    """Every table present and populated except `memories`, whose `content`
    column has been renamed -- the schema drift that would otherwise emit a
    plausible-looking export missing the three canonical memories."""
    con = sqlite3.connect(path)
    con.execute("create table memories (id integer primary key, body text, created_at text)")
    con.execute("create table episodic_memory (id integer primary key, content text, created_at text)")
    con.execute("create table working_memory (id integer primary key, content text, created_at text)")
    con.execute("insert into memories (body, created_at) values (?,?)",
                ("A canonical memory that must not vanish.", "2026-01-01T00:00:00"))
    con.execute("insert into episodic_memory (content, created_at) values (?,?)",
                ("Deployed the RTK rewrite.", "2026-09-19T10:00:00"))
    con.execute("insert into working_memory (content, created_at) values (?,?)",
                ("Uses Coolify.", "2026-09-19T13:00:00"))
    con.commit()
    con.close()


partial_tmp = tempfile.mkdtemp(prefix="mnemo-export-partial-")
partial_db = os.path.join(partial_tmp, "mnemosyne.db")
build_partial_store(partial_db)

print("\n8/9 a PARTIAL export (a table skipped) exits non-zero")
result = subprocess.run([sys.executable, SCRIPT, partial_db],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
check("non-zero exit although other tables exported fine",
      result.returncode != 0,
      "returncode=%d stderr=%r" % (result.returncode, result.stderr))
check("the error names the skipped table", "memories" in result.stderr,
      repr(result.stderr[-400:]))
check("the error names the underlying sqlite error",
      "content" in result.stderr, repr(result.stderr[-400:]))

print("\n9/9 --force downgrades the partial-export failure to a warning")
result = subprocess.run([sys.executable, SCRIPT, partial_db, "--force"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
check("zero exit with --force", result.returncode == 0,
      "returncode=%d stderr=%r" % (result.returncode, result.stderr))
check("the readable tables still made it to stdout",
      len([l for l in result.stdout.splitlines() if l.strip()]) == 2,
      repr(result.stdout))

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all mnemosyne export tests passed")

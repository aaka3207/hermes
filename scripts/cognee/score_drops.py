#!/usr/bin/env python3
"""Emit deletion candidates, using the same classification as corpus_shape.py.

Runs inside the cognee-backend container, read-only:

    docker cp scripts/cognee/score_drops.py <backend>:/tmp/ && \
        docker exec <backend> python /tmp/score_drops.py

corpus_shape.py reads Kuzu DocumentChunk text to produce counts. This reads the
Postgres `data` table instead, so every candidate carries the data_id and
dataset_id a /forget call needs, and writes them to /tmp/drop_candidates.json.

The classification is copied from corpus_shape.py deliberately, so the counts
here can be checked against the ones already published in
docs/cognee-corpus-shape.md. Two traps it exists to avoid:

* the import header must be stripped BEFORE the markers are applied -- the
  transcript marker is anchored, and `[mnemosyne] [tier | recorded ...]` sits
  in front of it;
* a session transcript is disposable by KIND, not by length. Requiring it to
  also be short matches almost nothing.
"""
import collections
import json
import os
import re
from urllib.parse import unquote, urlparse

import psycopg2

HEADER = re.compile(
    r"^\s*\[mnemosyne\]\s*\[(?P<tier>[a-z_]+)\s*\|\s*recorded\s*"
    r"(?P<ts>\d{4}-\d{2}-\d{2})[^\]]*\]\s*(?P<rest>.*)$", re.S)
SPEAKER = re.compile(r"^\s*\[(USER|ASSISTANT|[^\]]{1,40})\]\s*", re.I)
TRANSCRIPT = re.compile(r"^\s*Session ID:\s*hermes_", re.I)
MODEL_SLOT = re.compile(r"^model:[a-z_]+::", re.I)
DISCORD = re.compile(r"^\s*\[Triggering message id:", re.I)
DISCORD_STRIP = re.compile(
    r"^\s*\[Triggering message id:[^\]]*\]\s*(?:\[[^\]]{1,40}\]\s*)?", re.I)
TEST_NAMES = {"smoke", "prov"}
TEST_TEXT = re.compile(
    r"^(collision test [AB] |Model smoke test|Provenance smoke test)", re.I)
SHORT = 80


def body_of(text):
    m = HEADER.match(text)
    body = m.group("rest") if m else text
    tier = m.group("tier") if m else "(no header)"
    sm = SPEAKER.match(body)
    if sm:
        body = body[sm.end():]
    return tier, body.strip()


def marker_for(tier, body):
    if TRANSCRIPT.match(body):
        return "session transcript"
    if MODEL_SLOT.match(body):
        return "model-slot (durable)"
    if DISCORD.match(body):
        return "discord relay turn"
    if tier == "episodic_memory":
        return "episodic_memory (durable)"
    if len(body) < SHORT:
        return "short turn (<%d)" % SHORT
    return "unmarked, >=%d chars" % SHORT


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
    cur.execute("select id, name, raw_data_location, dataset_id from data")
    rows = cur.fetchall()

    marks = collections.Counter()
    buckets = collections.defaultdict(list)
    unreadable = 0

    for rid, name, loc, ds in rows:
        path = to_path(loc)
        if not path or not os.path.exists(path):
            unreadable += 1
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()

        tier, body = body_of(text)
        if ((name or "") in TEST_NAMES or (name or "").startswith("pasted-text-")
                or TEST_TEXT.match(body)):
            mark = "test artifact"
        else:
            mark = marker_for(tier, body)

        marks[mark] += 1
        rec = {"id": str(rid), "dataset_id": str(ds), "name": name, "mark": mark,
               "chars": len(body), "preview": " ".join(body.split())[:80]}
        if mark == "discord relay turn":
            rec["stripped_chars"] = len(DISCORD_STRIP.sub("", body).strip())
        buckets[mark].append(rec)

    print("records read: %d  (unreadable raw: %d)\n" % (len(rows) - unreadable, unreadable))
    for mark, n in marks.most_common():
        print("  %-30s %5d" % (mark, n))

    tier_a_marks = ["session transcript", "short turn (<%d)" % SHORT, "test artifact"]
    tier_a = [r for m in tier_a_marks for r in buckets.get(m, [])]
    relay_empty = [r for r in buckets.get("discord relay turn", [])
                   if r["stripped_chars"] < 60]

    print("\n  TIER A (no judgement): %d" % len(tier_a))
    print("  TIER B (relay, <60 chars once stripped): %d" % len(relay_empty))
    print("  A + B: %d of %d" % (len(tier_a) + len(relay_empty), len(rows) - unreadable))

    with open("/tmp/drop_candidates.json", "w") as fh:
        json.dump({"tier_a": tier_a, "tier_b": relay_empty}, fh, indent=1)
    print("\nwrote /tmp/drop_candidates.json")


main()

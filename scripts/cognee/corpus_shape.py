#!/usr/bin/env python3
"""Characterise the imported Mnemosyne corpus: what is in it, what is durable.

Runs inside the cognee-backend container, read-only against the live Kuzu
graph:

    docker cp scripts/cognee/corpus_shape.py <backend>:/tmp/ && \
        docker exec <backend> python /tmp/corpus_shape.py

No LLM -- everything is structural or lexical, so it is cheap, deterministic
and re-runnable. It exists so the re-seed cut can be argued from counts rather
than impressions, and so the same counts can be taken again afterwards to show
the cut did what it claimed.

Records carry an import header:

    [mnemosyne] [<tier> | recorded YYYY-MM-DD HH:MM:SS] [USER] <text>

so tier and record time parse out without guessing.

The marker populations below were not designed up front. They came from
hand-reading 40 records of the one population the coarse pass could not sort,
and finding that two prefixes separate durable from disposable mechanically.
See docs/cognee-corpus-shape.md.
"""
import collections
import glob
import json
import os
import re
import sys

import kuzu

HEADER = re.compile(
    r"^\s*\[mnemosyne\]\s*\[(?P<tier>[a-z_]+)\s*\|\s*recorded\s*"
    r"(?P<ts>\d{4}-\d{2}-\d{2})[^\]]*\]\s*(?P<rest>.*)$",
    re.S,
)
SPEAKER = re.compile(r"^\s*\[(USER|ASSISTANT|[^\]]{1,40})\]\s*", re.I)

# The three mechanical markers.
TRANSCRIPT = re.compile(r"^\s*Session ID:\s*hermes_", re.I)
MODEL_SLOT = re.compile(r"^model:[a-z_]+::", re.I)
DISCORD = re.compile(r"^\s*\[Triggering message id:", re.I)
DISCORD_STRIP = re.compile(
    r"^\s*\[Triggering message id:[^\]]*\]\s*(?:\[[^\]]{1,40}\]\s*)?", re.I
)

SHORT = 80


def load_bodies():
    """Return [(tier, body)] with the import header and speaker tag removed."""
    path = max(glob.glob("/cognee-storage/system/databases/*/*.pkl"), key=os.path.getsize)
    conn = kuzu.Connection(kuzu.Database(path, read_only=True))
    res = conn.execute(
        "MATCH (n:Node) WHERE n.type='DocumentChunk' RETURN n.id, n.properties ORDER BY n.id"
    )
    rows = []
    while res.has_next():
        _, props = res.get_next()
        try:
            text = (json.loads(props) or {}).get("text")
        except (TypeError, ValueError):
            text = None
        if not text:
            continue
        m = HEADER.match(text)
        tier = m.group("tier") if m else "(no header)"
        body = m.group("rest") if m else text
        sm = SPEAKER.match(body)
        if sm:
            body = body[sm.end():]
        rows.append((tier, body.strip()))
    return rows


def marker_for(tier, body):
    """Classify one record. Order matters: transcripts can also look long."""
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


def percentiles(values, labels=(("min", 0.0), ("p25", .25), ("median", .5),
                                ("p75", .75), ("p90", .9), ("max", 1.0))):
    vs = sorted(values)
    if not vs:
        return []
    return [(name, vs[min(len(vs) - 1, int(q * len(vs)))]) for name, q in labels]


def main():
    rows = load_bodies()
    n = len(rows)
    if not n:
        sys.exit("no chunk text found")
    print("records: %d\n" % n)

    tiers = collections.Counter()
    months = collections.Counter()
    marks = collections.Counter()
    chars_by_mark = collections.Counter()
    lens_by_mark = collections.defaultdict(list)
    samples = collections.defaultdict(list)

    # Record time comes off the raw text, before the header was stripped.
    path = max(glob.glob("/cognee-storage/system/databases/*/*.pkl"), key=os.path.getsize)
    conn = kuzu.Connection(kuzu.Database(path, read_only=True))
    res = conn.execute("MATCH (n:Node) WHERE n.type='DocumentChunk' RETURN n.properties")
    while res.has_next():
        (props,) = res.get_next()
        try:
            text = (json.loads(props) or {}).get("text") or ""
        except (TypeError, ValueError):
            text = ""
        m = HEADER.match(text)
        if m:
            months[m.group("ts")[:7]] += 1

    for tier, body in rows:
        tiers[tier] += 1
        mark = marker_for(tier, body)
        marks[mark] += 1
        chars_by_mark[mark] += len(body)
        lens_by_mark[mark].append(len(body))
        if len(samples[mark]) < 2:
            samples[mark].append(body[:130].replace("\n", " "))

    total_chars = sum(chars_by_mark.values()) or 1

    print("== tier ==")
    for k, v in tiers.most_common():
        print("  %-30s %5d (%4.1f%%)" % (k, v, 100.0 * v / n))
    print()

    print("== recorded by month ==")
    for k in sorted(months):
        print("  %-10s %5d" % (k, months[k]))
    print()

    print("== mechanical markers ==")
    print("  %-34s %6s %8s %9s %11s" % ("", "count", "% recs", "% chars", "median len"))
    for k, v in marks.most_common():
        med = sorted(lens_by_mark[k])[len(lens_by_mark[k]) // 2]
        print("  %-34s %6d %7.1f%% %8.1f%% %11d"
              % (k, v, 100.0 * v / n, 100.0 * chars_by_mark[k] / total_chars, med))
    print("  %-34s %6d" % ("TOTAL", n))
    print()

    print("== samples ==")
    for mark in marks:
        print("  -- %s" % mark)
        for s in samples[mark]:
            print("       %s" % s)
    print()

    # A discord relay record is mostly boilerplate; measure what is left.
    rest = [len(DISCORD_STRIP.sub("", b).strip())
            for t, b in rows if DISCORD.match(b) and not TRANSCRIPT.match(b)]
    if rest:
        print("== discord relay turns, scaffolding stripped ==")
        print("  records: %d" % len(rest))
        for name, val in percentiles(rest):
            print("  %-8s %5d chars" % (name, val))
        under = sum(1 for x in rest if x < 60)
        print("  under 60 chars of real content: %d (%.1f%%)"
              % (under, 100.0 * under / len(rest)))
        print()

    durable = marks["model-slot (durable)"] + marks["episodic_memory (durable)"]
    disposable = (marks["short turn (<%d)" % SHORT] + marks["session transcript"])
    print("== triage ==")
    print("  mechanically durable (model-slot + episodic): %5d" % durable)
    print("  mechanically disposable (short + transcript): %5d" % disposable)
    print("  discord relay (mostly disposable, see above):  %5d" % marks["discord relay turn"])
    print("  needs a judgement call (unmarked >=%d):        %5d"
          % (SHORT, marks["unmarked, >=%d chars" % SHORT]))


main()

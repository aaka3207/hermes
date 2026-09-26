#!/usr/bin/env python3
"""Score classification stability and group what is left for a human.

Runs inside the cognee-backend container after classify_unmarked.py has been
run three times, with its output copied to /tmp/pass{1,2,3}.jsonl:

    for i in 1 2 3; do python /tmp/classify_unmarked.py >/dev/null; \
        cp /tmp/unmarked_labels.jsonl /tmp/pass$i.jsonl; done
    python /tmp/triage_review.py

A single classification pass produces a number; it does not say whether that
number survives being taken again. It does not: the aggregate keep rate is
stable to about a point, but individual records flip. This reports the records
that agreed 3/3 (settled) separately from those that did not (a human decides),
then buckets the unsettled ones, because the classifier wavers on whole *kinds*
of record -- mostly operational prompt text that leaked into memory -- and
those are one policy call each rather than N judgements.

Buckets are deliberately conservative; anything not clearly recognisable falls
through to "prose (judge individually)".
"""
import collections
import json
import re
import sys

BUCKETS = [
    ("agent worker prompt (Gmail / Composio monitor)",
     re.compile(r"bounded.{0,20}(one-shot|read-only).{0,40}(worker|monitor)"
                r"|configured Composio MCP server", re.I)),
    ("scheduled cron-job prompt",
     re.compile(r"you are running as a scheduled cron job|DELIVERY: Your final response", re.I)),
    ("async delegation / system notice",
     re.compile(r"ASYNC DELEGATION|BATCH COMPLETE|deleg_[0-9a-f]", re.I)),
    ("Notion page/bookmark created",
     re.compile(r"app\.notion\.com/p/|was created (under|in) ", re.I)),
    ("one-off tool/skill invocation instruction",
     re.compile(r"^(use|run|call) the .{0,40}(skill|tool|api)\b"
                r"|helper script through the terminal", re.I)),
    ("workout / health observation",
     re.compile(r"\b(sets?|reps?|rdls|hamstring|soreness|workout|lifting|creatine)\b", re.I)),
]
FALLTHROUGH = "prose (judge individually)"


def bucket_for(text):
    for name, rx in BUCKETS:
        if rx.search(text):
            return name
    return FALLTHROUGH


def load_passes(n=3):
    out = []
    for i in range(1, n + 1):
        rows = {}
        with open("/tmp/pass%d.jsonl" % i) as fh:
            for line in fh:
                r = json.loads(line)
                rows[r["node"]] = r
        out.append(rows)
    return out


def main():
    passes = load_passes()
    nodes = sorted(set.intersection(*(set(p) for p in passes)))
    if not nodes:
        sys.exit("no overlapping records across passes")

    tally = collections.Counter()
    unstable = []
    for node in nodes:
        votes = [p[node]["keep"] for p in passes]
        keeps = sum(1 for v in votes if v)
        if keeps == len(passes):
            tally["stable KEEP"] += 1
        elif keeps == 0:
            tally["stable DROP"] += 1
        else:
            tally["unstable"] += 1
            unstable.append((keeps, node, passes[0][node]["text"]))

    n = len(nodes)
    print("records compared across %d passes: %d\n" % (len(passes), n))
    for k in ("stable KEEP", "stable DROP", "unstable"):
        print("  %-14s %4d (%4.1f%%)" % (k, tally[k], 100.0 * tally[k] / n))
    settled = tally["stable KEEP"] + tally["stable DROP"]
    print("\n  settled:       %d (%.1f%%)" % (settled, 100.0 * settled / n))
    print("  needs a human: %d (%.1f%%)" % (tally["unstable"], 100.0 * tally["unstable"] / n))

    print("\n== per-pass keep counts (the aggregate repeats; the records do not) ==")
    for i, p in enumerate(passes, 1):
        keeps = sum(1 for node in nodes if p[node]["keep"])
        print("  pass %d: %d keep (%.1f%%)" % (i, keeps, 100.0 * keeps / n))

    groups = collections.defaultdict(list)
    for keeps, _node, text in unstable:
        groups[bucket_for(text)].append((keeps, text))

    print("\n== decision clusters among the unstable ==")
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        lean_keep = sum(1 for k, _ in items if k > len(passes) / 2)
        print("\n  %s" % name)
        print("    %d records  (%d lean keep, %d lean drop)"
              % (len(items), lean_keep, len(items) - lean_keep))
        for k, text in items[:2]:
            print("      [%d/%d] %s" % (k, len(passes), text[:110].replace("\n", " ")))

    singles = groups.get(FALLTHROUGH, [])
    print("\n\n== the %d one-offs ==" % len(singles))
    for i, (k, text) in enumerate(sorted(singles, key=lambda x: -x[0]), 1):
        print("\n%2d. [%d/%d keep] %s"
              % (i, k, len(passes), text.strip().replace("\n", " ")[:260]))


main()

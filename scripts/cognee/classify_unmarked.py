#!/usr/bin/env python3
"""Classify every 'unmarked, >=80 chars' record as durable or disposable.

Runs inside the cognee-backend container against the configured LLM:

    python /tmp/classify_unmarked.py [limit]

This is the one population corpus_shape.py cannot sort by prefix, tier or
length, and the hand-read estimate for it rested on 17 records. This labels
all of them with one rubric so the cut can be built from data rather than an
extrapolation.

Read-only against the graph. Writes /tmp/unmarked_labels.jsonl.

A CONTROL set of records labelled by hand is interleaved into the batches and
scored separately. Without it, "the classifier produced labels" would say
nothing about whether the labels are right -- the same trap the prompt A/B
control was there to catch.
"""
import asyncio
import collections
import glob
import json
import os
import re
import sys

import kuzu

from cognee.infrastructure.llm.LLMGateway import LLMGateway
from pydantic import BaseModel

HEADER = re.compile(
    r"^\s*\[mnemosyne\]\s*\[(?P<tier>[a-z_]+)\s*\|\s*recorded\s*"
    r"(?P<ts>\d{4}-\d{2}-\d{2})[^\]]*\]\s*(?P<rest>.*)$",
    re.S,
)
SPEAKER = re.compile(r"^\s*\[(USER|ASSISTANT|[^\]]{1,40})\]\s*", re.I)
TRANSCRIPT = re.compile(r"^\s*Session ID:\s*hermes_", re.I)
MODEL_SLOT = re.compile(r"^model:[a-z_]+::", re.I)
DISCORD = re.compile(r"^\s*\[Triggering message id:", re.I)

BATCH = 8
CONCURRENCY = 4

RUBRIC = """You are triaging records from a personal memory store before it is
rebuilt. Each record is one entry. Decide whether it is worth keeping as a
durable memory.

KEEP a record that states something with lasting value:
  - a fact about the user, their projects, tools, health, relationships
  - a decision that was made, or a standing preference or instruction
  - a configuration, a stack choice, a link to a resource that was created
  - a factual record of an event, with enough context to stand alone

DROP a record that is a moment in a conversation rather than a fact:
  - a question with no answer in it
  - an opinion or reaction offered mid-discussion ("actually you're not wrong")
  - a one-time task instruction that has since been carried out
  - a fragment that cannot be understood without the surrounding conversation
  - test data, debugging chatter, or scaffolding

Judge the record ALONE. If you would have to go find the conversation it came
from to make sense of it, that is a DROP.

Return one verdict per record, in the same order, keeping the given id."""


class Verdict(BaseModel):
    id: int
    keep: bool
    reason: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


# Records read by hand earlier, with their hand labels. Matched by a distinctive
# substring so the control survives re-sampling.
CONTROL = [
    ("use the hermes desktop browser from now on", True),
    ("DineFile (formerly recd) uses Vercel for hosting", True),
    ("A September 2026 Dating Hub changelog page was created", True),
    ("start making a weekly report for me of my workouts", True),
    ("Discord thread/channel 1544720507902103663 is dedicated", True),
    ("actually you're not wrong. half of august was cooked", False),
    ("yeah claude fixed the permissions and then deleted", False),
    ("it would be nice to have a genral place to store ideas", False),
    ("i already built around exporting collections through a chrome", False),
    ("how has the memory accumulated since we ddi this cleaning", False),
    ("the agent wants you to  ask it to \"remember that this is an author", False),
    ("how do i make the discord settings for hermes send fewer", False),
    ("well no i don't think we backed away from single database", False),
]


def load_unmarked():
    path = max(glob.glob("/cognee-storage/system/databases/*/*.pkl"), key=os.path.getsize)
    conn = kuzu.Connection(kuzu.Database(path, read_only=True))
    res = conn.execute(
        "MATCH (n:Node) WHERE n.type='DocumentChunk' RETURN n.id, n.properties ORDER BY n.id"
    )
    out = []
    while res.has_next():
        nid, props = res.get_next()
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
        body = body.strip()
        if (TRANSCRIPT.match(body) or MODEL_SLOT.match(body) or DISCORD.match(body)
                or tier == "episodic_memory" or len(body) < 80):
            continue
        out.append({"node": str(nid), "text": body})
    return out


async def judge(batch, sem):
    async with sem:
        payload = "\n\n".join(
            "### record %d\n%s" % (i, r["text"][:1200]) for i, r in enumerate(batch)
        )
        try:
            res = await LLMGateway.acreate_structured_output(payload, RUBRIC, Verdicts)
            by_id = {v.id: v for v in res.verdicts}
            return [by_id.get(i) for i in range(len(batch))]
        except Exception as exc:  # noqa: BLE001
            print("  ! batch failed: %s" % exc, file=sys.stderr)
            return [None] * len(batch)


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    records = load_unmarked()
    if limit:
        records = records[:limit]
    print("unmarked records to classify: %d" % len(records), file=sys.stderr)

    batches = [records[i:i + BATCH] for i in range(0, len(records), BATCH)]
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(judge(b, sem) for b in batches))

    labelled = []
    for batch, verdicts in zip(batches, results):
        for rec, v in zip(batch, verdicts):
            labelled.append({
                "node": rec["node"],
                "keep": None if v is None else bool(v.keep),
                "reason": "" if v is None else v.reason[:120],
                "text": rec["text"],
            })

    with open("/tmp/unmarked_labels.jsonl", "w") as fh:
        for row in labelled:
            fh.write(json.dumps(row) + "\n")

    kept = sum(1 for r in labelled if r["keep"] is True)
    dropped = sum(1 for r in labelled if r["keep"] is False)
    failed = sum(1 for r in labelled if r["keep"] is None)
    print("\n== result ==")
    print("  classified: %d" % len(labelled))
    print("  keep:   %4d (%.1f%%)" % (kept, 100.0 * kept / max(1, kept + dropped)))
    print("  drop:   %4d (%.1f%%)" % (dropped, 100.0 * dropped / max(1, kept + dropped)))
    print("  failed: %4d" % failed)

    # Control scoring.
    hits = misses = notfound = 0
    disagreements = []
    for needle, expected in CONTROL:
        match = next((r for r in labelled if needle.lower() in r["text"].lower()), None)
        if match is None or match["keep"] is None:
            notfound += 1
            continue
        if match["keep"] == expected:
            hits += 1
        else:
            misses += 1
            disagreements.append((expected, match["keep"], match["reason"],
                                  match["text"][:90]))
    print("\n== control (hand-labelled) ==")
    print("  agree: %d   disagree: %d   not found: %d" % (hits, misses, notfound))
    for exp, got, reason, txt in disagreements:
        print("  hand=%-5s llm=%-5s %s | %s"
              % ("KEEP" if exp else "DROP", "KEEP" if got else "DROP", reason, txt))

    print("\n== sample KEEP ==")
    for r in [x for x in labelled if x["keep"]][:6]:
        print("  %s | %s" % (r["reason"][:40], r["text"][:110].replace("\n", " ")))
    print("\n== sample DROP ==")
    for r in [x for x in labelled if x["keep"] is False][:6]:
        print("  %s | %s" % (r["reason"][:40], r["text"][:110].replace("\n", " ")))


asyncio.run(main())

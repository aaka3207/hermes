#!/usr/bin/env python3
"""A/B the default cognee extraction prompt against a candidate, in-container.

Runs inside the cognee-backend container, where cognee and the configured LLM
credentials already live:

    python /tmp/prompt_ab.py /tmp/sample_chunks.json /tmp/extraction_prompt.txt

Read-only with respect to the graph. It calls ``extract_content_graph``
directly -- the same function the cognify pipeline calls -- and never reaches
``add_data_points``, so nothing is persisted. It does spend LLM tokens: two
extractions per sample record.

The report counts the four things the graph analysis flagged: predicate
fragmentation, date nodes, import residue, and identity splitting.
"""
import asyncio
import collections
import json
import re
import sys

from cognee.infrastructure.llm.extraction.knowledge_graph.extract_content_graph import (
    extract_content_graph,
)
from cognee.shared.data_models import KnowledgeGraph

CONCURRENCY = 4

# Names that should never become nodes. DATE_RE also catches bare years and
# clock times, which is why it is anchored rather than a substring search.
DATE_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}([ t]\d{2}:\d{2}(:\d{2})?)?|\d{4}|\d{1,2}:\d{2}(:\d{2})?"
    r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s*\d{0,4})\s*$",
    re.I,
)
RESIDUE = {
    "mnemosyne", "working_memory", "working memory", "episodic_memory",
    "episodic memory", "semantic_memory", "semantic memory", "procedural_memory",
    "user", "the user", "record", "memory",
}
IDENTITY_RE = re.compile(r"^(ameer|ameer[ _]akashe|user|the user|aakash\w*)$", re.I)

VOCAB = {
    "is_a", "part_of", "has_property", "has_status", "has_value",
    "uses", "built_with", "depends_on", "integrates_with", "replaces",
    "deployed_on", "hosted_on",
    "works_at", "works_on", "owns", "created_by", "collaborates_with",
    "prefers", "avoids", "prohibits", "requires", "decided_to", "plans_to",
    "located_in", "traveled_to", "attended",
    "experienced", "treated_with", "measured_as",
    "asks_about", "reported", "records", "saved_to",
}

# Predicates that only assert "these two are connected". Tracked separately
# because a vocabulary can shrink by consolidating meaning or by dumping it
# into a catch-all, and only the second is a regression.
VAGUE = {"related_to", "mentions", "associated_with", "connected_to", "refers_to"}


async def extract(text, prompt, sem):
    async with sem:
        try:
            return await extract_content_graph(text, KnowledgeGraph, custom_prompt=prompt)
        except Exception as exc:  # noqa: BLE001 -- one bad record must not sink the run
            print("  ! extraction failed: %s" % exc, file=sys.stderr)
            return None


def tally(graphs):
    preds = collections.Counter()
    nodes, date_nodes, residue_nodes, identity = [], [], [], collections.Counter()
    n_edges = 0
    for g in graphs:
        if g is None:
            continue
        for node in g.nodes:
            name = (node.name or node.id or "").strip()
            nodes.append(name)
            if DATE_RE.match(name):
                date_nodes.append(name)
            if name.lower() in RESIDUE:
                residue_nodes.append(name)
            if IDENTITY_RE.match(name):
                identity[name.lower()] += 1
        for edge in g.edges:
            preds[edge.relationship_name] += 1
            n_edges += 1
    return {
        "graphs": sum(1 for g in graphs if g is not None),
        "nodes": len(nodes),
        "edges": n_edges,
        "distinct_predicates": len(preds),
        "singleton_predicates": sum(1 for _, c in preds.items() if c == 1),
        "in_vocab_edges": sum(c for p, c in preds.items() if p in VOCAB),
        "vague_edges": sum(c for p, c in preds.items() if p in VAGUE),
        "max_pred_words": max((p.count("_") + 1 for p in preds), default=0),
        "long_predicates": sorted(p for p in preds if p.count("_") + 1 > 2),
        "date_nodes": len(date_nodes),
        "date_examples": sorted(set(date_nodes))[:8],
        "residue_nodes": len(residue_nodes),
        "residue_examples": sorted(set(residue_nodes))[:8],
        "identity_spellings": dict(identity),
        "top_predicates": preds.most_common(12),
    }


def pct(part, whole):
    return "n/a" if not whole else "%.0f%%" % (100.0 * part / whole)


def report(label, t):
    print("\n== %s ==" % label)
    print("  graphs %d | nodes %d | edges %d" % (t["graphs"], t["nodes"], t["edges"]))
    print("  distinct predicates: %d  (singletons %d, %s of the vocabulary)"
          % (t["distinct_predicates"], t["singleton_predicates"],
             pct(t["singleton_predicates"], t["distinct_predicates"])))
    print("  edges using the closed vocabulary: %d (%s)"
          % (t["in_vocab_edges"], pct(t["in_vocab_edges"], t["edges"])))
    print("  vague ('these are connected') edges: %d (%s)"
          % (t["vague_edges"], pct(t["vague_edges"], t["edges"])))
    print("  longest predicate: %d words; >2 words: %s"
          % (t["max_pred_words"], t["long_predicates"][:6] or "none"))
    print("  date nodes: %d %s" % (t["date_nodes"], t["date_examples"]))
    print("  residue nodes: %d %s" % (t["residue_nodes"], t["residue_examples"]))
    print("  identity spellings: %s" % (t["identity_spellings"] or "none"))
    print("  top predicates: %s" % (t["top_predicates"],))


async def main():
    sample_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/sample_chunks.json"
    prompt_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/extraction_prompt.txt"

    with open(sample_path) as fh:
        sample = json.load(fh)
    with open(prompt_path) as fh:
        candidate_prompt = fh.read()

    texts = [s["text"] for s in sample]
    print("A/B over %d records, concurrency %d" % (len(texts), CONCURRENCY), file=sys.stderr)

    sem = asyncio.Semaphore(CONCURRENCY)
    base = await asyncio.gather(*(extract(t, None, sem) for t in texts))
    print("baseline done", file=sys.stderr)
    cand = await asyncio.gather(*(extract(t, candidate_prompt, sem) for t in texts))
    print("candidate done", file=sys.stderr)

    tb, tc = tally(base), tally(cand)
    report("BASELINE (generate_graph_prompt.txt)", tb)
    report("CANDIDATE (extraction_prompt.txt)", tc)

    with open("/tmp/prompt_ab_result.json", "w") as fh:
        json.dump({"baseline": tb, "candidate": tc}, fh, indent=1, default=str)
    print("\nwrote /tmp/prompt_ab_result.json")


asyncio.run(main())

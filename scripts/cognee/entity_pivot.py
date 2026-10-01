#!/usr/bin/env python3
"""Prototype: use the graph as an index into chunks, with no LLM at recall time.

    docker cp scripts/cognee/graph_vs_chunks.py <hermes>:/tmp/
    docker cp scripts/cognee/entity_pivot.py <hermes>:/tmp/
    docker exec -d -u hermes <hermes> sh -c \
        'python3 /tmp/entity_pivot.py > /tmp/entity_pivot.out 2>&1'

Read-only (CHUNKS recalls and CYPHER reads). Needs graph_vs_chunks.py beside it
for the questions and scoring. Never prints the key.

Cognee 1.6.1 has no mode that does this. ``HYBRID_COMPLETION`` searches chunks
and entities side by side and merges the results, but never follows an entity
to the chunks that mention it. The graph has those links: every
``DocumentChunk`` has ``contains`` edges to its entities.

The lookup:

1. Once: every entity name with the number of chunks linked to it (its
   document frequency, df), via one CYPHER query. A real implementation would
   cache this and refresh it after writes.
2. Per question: an entity matches when each of its name's words (4+ letters,
   possessive stripped) is a word of the question, or when a 5+ letter word of
   its name is. Each match counts ``idf = log(1 + chunks / df)`` times the share
   of its words matched, so a name in every chunk (``ameer``) counts for almost
   nothing.
3. One CYPHER query fetches the chunks linked to the matched entities; each
   chunk scores the sum of its matched entities' weights.

The report compares, at depth k = 2, 3 and 5, ``CHUNKS`` alone with ``CHUNKS``
top k plus the lookup's top k. Low k stands in for a crowded store, where the
right record falls below the cut. The lookup matches names by their words, so
the control is ``CHUNKS`` plus ``CHUNKS_LEXICAL`` (keyword search) at the same
depth: if the lookup does no better than that, the graph adds nothing.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request as u

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_vs_chunks import CONFIG, QUESTIONS, hits, norm  # noqa: E402

STOP = {"what", "when", "where", "which", "who", "whom", "whose", "why", "how", "does",
        "did", "is", "are", "was", "the", "and", "for", "with", "that", "this", "from",
        "about", "know", "have", "has", "his", "her", "their", "taking", "take",
        "roughly", "much", "many", "say", "said", "stay", "matter"}


def words(text, min_len):
    toks = re.findall(r"[a-z0-9][a-z0-9'-]*", norm(text))
    out = set()
    for t in toks:
        t = re.sub(r"'s$|'$", "", t)
        if len(t) >= min_len and t not in STOP:
            out.add(t)
            if t.endswith("s") and len(t) > min_len:
                out.add(t[:-1])
    return out


class Graph:
    def __init__(self, cfg):
        self.base = cfg["service_url"]
        self.hdr = {"X-Api-Key": cfg["api_key"], "Content-Type": "application/json"}

    def post(self, body):
        req = u.Request(self.base + "/api/v1/recall", method="POST", headers=self.hdr,
                        data=json.dumps(body).encode())
        return json.load(u.urlopen(req, timeout=120))

    def cypher(self, q):
        res = self.post({"query": q, "search_type": "CYPHER", "datasets": ["shared"],
                         "scope": ["graph"]})
        return [[cell["value"] for cell in r["raw"]["value"]] for r in res]

    def chunks(self, query, k, search_type="CHUNKS"):
        res = self.post({"query": query, "search_type": search_type, "datasets": ["shared"],
                         "scope": ["graph"], "top_k": k})
        return [(r.get("raw") or {}).get("id") or r.get("text", "")[:80] for r in res], \
               [r.get("text", "") for r in res]


def build_index(g):
    rows = g.cypher(
        "MATCH (ch:Node)-[r:EDGE]->(e:Node) WHERE ch.type = 'DocumentChunk' AND e.type = 'Entity' "
        "AND r.relationship_name = 'contains' RETURN e.id, e.name, count(ch)")
    n_chunks = g.cypher("MATCH (n:Node) WHERE n.type = 'DocumentChunk' RETURN count(n)")[0][0]
    return [(eid, name, df) for eid, name, df in rows], n_chunks


def match_entities(question, index, n_chunks):
    q4, matched = words(question, 4), {}
    for eid, name, df in index:
        nw = words(name, 4)
        if not nw:
            continue
        share = len(nw & q4) / len(nw)
        if share < 1 and not any(len(w) >= 5 and w in q4 for w in nw):
            continue
        matched[eid] = (name, share * math.log(1 + n_chunks / df))
    return matched


def pivot(g, question, index, n_chunks, k):
    matched = match_entities(question, index, n_chunks)
    if not matched:
        return [], [], []
    ids = ", ".join("'%s'" % i for i in matched)
    rows = g.cypher(
        "MATCH (ch:Node)-[r:EDGE]->(e:Node) WHERE ch.type = 'DocumentChunk' "
        "AND r.relationship_name = 'contains' AND e.id IN [%s] RETURN ch.id, e.id, ch.properties" % ids)
    score, text = {}, {}
    for cid, eid, props in rows:
        score[cid] = score.get(cid, 0.0) + matched[eid][1]
        text[cid] = json.loads(props).get("text", "")
    top = sorted(score, key=score.get, reverse=True)[:k]
    names = sorted({matched[e][0] for e in matched}, key=lambda n: -max(
        w for i, (nm, w) in matched.items() if nm == n))
    return top, [text[c] for c in top], names


def main():
    g = Graph(json.load(open(CONFIG)))
    t0 = time.time()
    index, n_chunks = build_index(g)
    print("index: %d entities over %d chunks in %.1fs\n" % (len(index), n_chunks, time.time() - t0))

    ks = (2, 3, 5)
    totals = {(m, k): 0 for m in ("chunks", "pivot", "union", "lexical") for k in ks}
    for kind, q, facts in QUESTIONS:
        _, ctexts = g.chunks(q, 5)
        _, ltexts = g.chunks(q, 5, "CHUNKS_LEXICAL")
        t1 = time.time()
        _, ptexts, names = pivot(g, q, index, n_chunks, 5)
        pdt = time.time() - t1
        cells = []
        for k in ks:
            c = all(hits("\n".join(ctexts[:k]), facts))
            p = all(hits("\n".join(ptexts[:k]), facts))
            un = all(hits("\n".join(ctexts[:k] + ptexts[:k]), facts))
            lx = all(hits("\n".join(ctexts[:k] + ltexts[:k]), facts))
            totals[("chunks", k)] += c
            totals[("pivot", k)] += p
            totals[("union", k)] += un
            totals[("lexical", k)] += lx
            cells.append("k%d %s%s%s%s" % (k, "C" if c else "-", "P" if p else "-",
                                          "U" if un else "-", "L" if lx else "-"))
        print("%-10s %s  %.1fs | %s | entities: %s" % (
            kind, "  ".join(cells), pdt, q, ", ".join(names[:5]) or "(none)"), flush=True)

    print("\nquestions fully answered (of %d)" % len(QUESTIONS))
    print("C = CHUNKS, P = lookup, U = CHUNKS+lookup, L = CHUNKS+CHUNKS_LEXICAL")
    print("k   CHUNKS  lookup  CHUNKS+lookup  CHUNKS+lexical")
    for k in ks:
        print("%d   %5d   %5d   %8d      %8d" % (k, totals[("chunks", k)], totals[("pivot", k)],
                                              totals[("union", k)], totals[("lexical", k)]))


if __name__ == "__main__":
    main()

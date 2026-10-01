#!/usr/bin/env python3
"""Graph versus CHUNKS on the live `shared` dataset, called the way Hermes calls it.

    docker cp scripts/cognee/graph_vs_chunks.py <hermes>:/tmp/
    docker exec -d -u hermes <hermes> sh -c \
        'python3 /tmp/graph_vs_chunks.py > /tmp/graph_vs_chunks.out 2>&1'

Read-only: recall calls only. Takes about two minutes, longer than an ssh-mcp
command's 60s limit, so run it detached and read the .out file. Full results go
to /tmp/graph_vs_chunks.json. Never prints the key.

Every call is ``POST /api/v1/recall`` on ``shared`` with ``scope: ["graph"]`` and
``top_k: 5``, as the plugin sends it:

* ``chunks``: ``CHUNKS``, the default ``cognee_recall``.
* ``graph_ctx``: ``GRAPH_COMPLETION`` + ``only_context``, ``cognee_recall
  context_only``.
* ``hybrid_ctx``: ``HYBRID_COMPLETION`` + ``only_context``, the per-prompt
  ``## Cognee Memory`` block. Its context is a ``## Relevant passages`` section
  (chunk retrieval) followed by graph sections; the report says when a fact was
  found only in the graph part.
* ``graph_llm``: ``GRAPH_COMPLETION``, an LLM-written answer, for reference.

Each question lists required facts, each a list of acceptable substrings. The
score is the fraction found in the returned text after Unicode normalisation (LLM
answers use non-breaking spaces and Unicode hyphens). The questions were written
from the records in ``shared`` on 2026-10-01; when the store changes, check each
answer is still stored before trusting a miss.
"""
import json
import re
import time
import unicodedata
import urllib.request as u

CONFIG = "/opt/data/cognee.json"

MODES = {
    "chunks": {"search_type": "CHUNKS"},
    "graph_ctx": {"search_type": "GRAPH_COMPLETION", "only_context": True},
    "hybrid_ctx": {"search_type": "HYBRID_COMPLETION", "only_context": True},
    "graph_llm": {"search_type": "GRAPH_COMPLETION"},
}

QUESTIONS = [
    ("single", "Who is Ameer's manager at Livefront?", [["fishwick"]]),
    ("single", "What antibiotic is Ameer allergic to?", [["amoxicillin"]]),
    ("single", "How much are Ameer's East Bank Club monthly dues?", [["295"]]),
    ("single", "What is Ameer's daily protein target during the fall build?", [["130"]]),
    ("single", "Which lights stay on when I say TV lights?", [["nightstand"], ["bedroom"]]),
    ("single", "What is Ameer Akashe's father's name?", [["ahmad"]]),
    ("multi", "Why is Ameer taking PTO on Friday October 23?", [["yasmeen"], ["wedding"]]),
    ("supersede", "When is Ameer's nasal procedure scheduled?", [["nov 2", "november 2"]]),
    ("multi", "Who is Ameer's Kitchen Cubes collaborator and what was his role at Incued?",
     [["furstoss"], ["ceo", "co-founder", "chief executive"]]),
    ("multi", "Who did Ameer go to New York with in September, and roughly how much did he spend?",
     [["luke"], ["950"]]),
    ("multi", "Who is the CEO of the company that bought Ameer's startup?", [["phil sands"]]),
    ("multi", "What medications does Ameer take that matter on his ENT procedure day?",
     [["vyvanse"], ["valium", "clonazepam"]]),
    ("relational", "Who is Josh Furstoss married to?", [["sarah"]]),
    ("relational", "How do Ameer's Monarch tools reach Hermes?", [["metamcp"]]),
    ("broad", "What do we know about Joyce?", [["adyen"], ["oct 1", "october 1"]]),
    ("broad", "What services run on Ameer's home server?",
     [["metamcp"], ["n8n"], ["home assistant"]]),
    ("broad", "Who is Yasmeen?", [["sister"], ["wedding", "oct 23"]]),
]


def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    return re.sub(r"\s+", " ", s).lower()


def hits(text, facts):
    t = norm(text)
    return [any(alt in t for alt in f) for f in facts]


def item_texts(res):
    if isinstance(res, list):
        return [str(r.get("text", "")) if isinstance(r, dict) else str(r) for r in res]
    return [] if res is None else [str(res)]


def main():
    cfg = json.load(open(CONFIG))
    hdr = {"X-Api-Key": cfg["api_key"], "Content-Type": "application/json"}

    def recall(query, mode):
        body = {"query": query, "datasets": ["shared"], "scope": ["graph"], "top_k": 5}
        body.update(MODES[mode])
        req = u.Request(cfg["service_url"] + "/api/v1/recall", method="POST", headers=hdr,
                        data=json.dumps(body).encode())
        t0 = time.time()
        try:
            res, err = json.load(u.urlopen(req, timeout=120)), None
        except Exception as e:
            res, err = None, str(e)[:200]
        return res, err, time.time() - t0

    rows, full = [], []
    for kind, q, facts in QUESTIONS:
        row = {"kind": kind, "q": q}
        for mode in MODES:
            res, err, dt = recall(q, mode)
            texts = item_texts(res)
            found = hits("\n".join(texts), facts)
            cell = {"score": sum(found) / len(facts), "s": dt, "chars": sum(map(len, texts)), "err": err}
            if mode == "chunks":
                # 1-based rank of the first chunk holding each fact; 0 = not returned.
                cell["ranks"] = [next((i + 1 for i, t in enumerate(texts) if hits(t, [f])[0]), 0)
                                 for f in facts]
            if mode == "hybrid_ctx" and texts:
                parts = re.split(r"\n## (?!Relevant passages)", texts[0], maxsplit=1)
                passages, graph = (parts[0], parts[1]) if len(parts) == 2 else (texts[0], "")
                cell["graph_only"] = [g and not p for g, p in
                                      zip(hits(graph, facts), hits(passages, facts))]
            row[mode] = cell
            full.append({"q": q, "mode": mode, "found": found, "err": err, "result": res})
        rows.append(row)
        print("%-10s %s | chunk ranks %s | %s" % (
            kind, " ".join("%s=%.2f/%.1fs" % (m, row[m]["score"], row[m]["s"]) for m in MODES),
            row["chunks"]["ranks"], q), flush=True)

    json.dump(full, open("/tmp/graph_vs_chunks.json", "w"), ensure_ascii=False, indent=1)

    print("\nmode        mean  full    median_s  median_chars  errors")
    for mode in MODES:
        sc = [r[mode]["score"] for r in rows]
        ss = sorted(r[mode]["s"] for r in rows)
        cs = sorted(r[mode]["chars"] for r in rows)
        print("%-10s  %.2f  %2d/%d    %5.1f     %7d       %d" % (
            mode, sum(sc) / len(sc), sum(s == 1 for s in sc), len(sc),
            ss[len(ss) // 2], cs[len(cs) // 2], sum(1 for r in rows if r[mode]["err"])))

    print("\ngraph context beat CHUNKS on:")
    for r in rows:
        for m in ("graph_ctx", "hybrid_ctx"):
            if r[m]["score"] > r["chunks"]["score"]:
                print("  ", m, r["q"])
    print("facts only in hybrid's graph sections (check by hand whether they answer):")
    for r in rows:
        if any(r["hybrid_ctx"].get("graph_only", [])):
            print("  ", r["q"])
    print("CHUNKS needed a rank above 3 for:")
    for r in rows:
        if max(r["chunks"]["ranks"]) > 3 or 0 in r["chunks"]["ranks"]:
            print("  ", r["chunks"]["ranks"], r["q"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Score OpenViking `find` retrieval against a gold set of verbatim evidence quotes.

    python3 scripts/ov_import/eval.py --cases dev.json --cases heldout.json \
        --arm v1=viking://resources/hubs/career-strategy \
        --arm v2=viking://resources/hubs/career-strategy-v2 \
        --arm v2f=viking://resources/hubs/career-strategy-v2+filters \
        --cache <dir> --out results.json [--runs 3] [--refresh-cache]

Gold cases: {"case_id", "query", "evidence": [{"page_id", "quote"}], ...}.
Scoring is quote containment (whitespace collapsed, Markdown backslash escapes removed,
curly quotes straightened):
  strict   a level-2 file in the top k contains the quote;
  doc      a level-2 file of the source document containing the quote (document = the
           frontmatter notion_page_id, else the parsed-document folder): the ledger's
           exact-source rule;
  lenient  also a level-0/1 directory hit whose direct files contain it.
A case is complete at k when every evidence quote is hit. Each arm's tree is cached
locally once (glob + download) to score hits and to mark evidence absent from the
corpus, which is reported separately (a tree built earlier can predate the evidence).

An arm URI ending in "+filters" runs the filtered recipe: when the query names a date
or month, find with tags entry_date=... then month=..., then plain find restricted to
level 2; results merged in that order, de-duplicated. An arm URI ending in "+files" runs
plain find restricted to level 2 (files only, no directory summaries). "+search" runs
`search` (reranked when the server has a reranker) with score_threshold -1. "+agent" runs
a fixed multi-step recipe standing in for an agent, scored by the order files are read:
round-robin over (a) search, expanding each directory hit with a files-only find scoped
to it, (b) grep for the names and figures in the query, files ranked by how many terms
they match, (c) find with the query's entry_date / month tags. "+grep" runs grep only:
the query's names and figures plus the frontmatter `date:` line for a named day or month,
files ranked by how many of these patterns they match.
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ovclient import OV, OVError  # noqa: E402

KS = (5, 10, 20)
# Capitalized only, so the verb "may" is not read as a month.
MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
DATE_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})\b")
MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b")
YEAR = 2026


def norm(text):
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!$~<>:|=])", r"\1", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def date_filters(query):
    tags = []
    for m, d in DATE_RE.findall(query):
        tags.append(f"entry_date={YEAR}-{MONTHS[m]:02d}-{int(d):02d}")
    months = {MONTHS[m] for m in MONTH_RE.findall(query)}
    tags += [f"month={YEAR}-{m:02d}" for m in sorted(months)]
    return list(dict.fromkeys(tags))


def cache_tree(ov, base, cache, refresh):
    path = cache / (re.sub(r"[^a-z0-9]+", "_", base.lower()) + ".json")
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    res = ov._req("POST", "/api/v1/search/glob", body={"pattern": "**/*.md", "uri": base, "node_limit": 100000})
    uris = [m if isinstance(m, str) else m["uri"] for m in res.get("matches", [])]
    # Directories can be named *.md (v1 parsed documents); glob returns them with a trailing "/".
    uris = [u for u in uris if not u.endswith("/") and not u.rsplit("/", 1)[-1].startswith(".")]

    def get(u):
        for attempt in range(5):
            try:
                return u, ov.download(u).decode("utf-8", "replace")
            except Exception:  # connection resets as well as HTTP errors
                time.sleep(2 * (attempt + 1))
        raise OVError(f"download failed: {u}")

    with ThreadPoolExecutor(4) as pool:
        files = dict(pool.map(get, uris))
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(files, ensure_ascii=False))
    return files


QWORDS = set("""What Which Who Whom Where When Why How Did Does Do Is Was Were Are In On For During
According After Before The A An My I I'm And Or Of To From With At By As Any Anything""".split())
TERM_RE = re.compile(r"(?:[A-Z][\w&'.\-]*)(?:\s+(?:[A-Z][\w&'.\-]*|&))*(?:\s+\d[\d\u2013\-]*)?|\$\d[\d,.]*[KkMm]?")


def query_terms(query):
    """Names and figures an agent would grep for: capitalized runs and dollar amounts,
    minus question words and month names (dates go through tags)."""
    out = []
    for m in TERM_RE.findall(query):
        words = [w for w in re.sub(r"'s\b|[?,.:;]+$", "", m).split()]
        while words and (words[0] in QWORDS or words[0] in MONTHS):
            words.pop(0)
        term = " ".join(words).strip(" .,'")
        if len(term) >= 3 and term not in MONTHS:
            out.append(term)
    return list(dict.fromkeys(out))


def search(ov, base, query, limit, **extra):
    body = {"query": query, "target_uri": base, "limit": limit, "score_threshold": -1, **extra}
    return ov._req("POST", "/api/v1/search/search", body=body).get("resources", [])


def agent_recipe(ov, base, query, limit=20):
    def safe(fn):
        try:
            return fn()
        except OVError:
            return []
    a = []
    for hit in safe(lambda: search(ov, base, query, 10)):
        if hit.get("level") == 2:
            a.append(hit)
        else:
            d = hit["uri"].rsplit("/", 1)[0] if hit["uri"].rsplit("/", 1)[-1].startswith(".") else hit["uri"]
            a += safe(lambda: ov.find(query, target_uri=d, limit=3, level=2).get("resources", []))
    counts, order = {}, []
    for term in query_terms(query):
        res = safe(lambda: ov.grep(base, re.escape(term), case_insensitive=True, node_limit=300))
        for u in dict.fromkeys(m["uri"] for m in (res.get("matches", []) if isinstance(res, dict) else [])):
            if u not in counts:
                order.append(u)
            counts[u] = counts.get(u, 0) + 1
    b = [{"uri": u, "level": 2} for u in sorted(order, key=lambda u: (-counts[u], order.index(u)))]
    c = []
    for tag in date_filters(query):
        c += safe(lambda: ov.find(query, target_uri=base, limit=10, tags=[tag], level=2).get("resources", []))
    out, seen, streams = [], set(), [a, b, c]
    while any(streams) and len(out) < limit:
        for st in streams:
            while st:
                h = st.pop(0)
                if h["uri"] not in seen:
                    seen.add(h["uri"])
                    out.append(h)
                    break
    return out[:limit]


def grep_recipe(ov, base, query, limit=20):
    """grep only: each name/figure in the query (case-insensitive), plus the frontmatter
    `date:` line for a named day (or month); files ranked by how many of these they match."""
    patterns = [re.escape(t) for t in query_terms(query)]
    for m, d in DATE_RE.findall(query):
        patterns.append(f"^date: '{YEAR}-{MONTHS[m]:02d}-{int(d):02d}'")
    if not DATE_RE.search(query):
        patterns += [f"^date: '{YEAR}-{MONTHS[m]:02d}-" for m in dict.fromkeys(MONTH_RE.findall(query))]
    counts, order = {}, []
    for p in patterns:
        try:
            res = ov.grep(base, p, case_insensitive=True, node_limit=300)
        except OVError:
            continue
        for u in dict.fromkeys(m["uri"] for m in res.get("matches", [])):
            if u not in counts:
                order.append(u)
            counts[u] = counts.get(u, 0) + 1
    ranked = sorted(order, key=lambda u: (-counts[u], order.index(u)))
    return [{"uri": u, "level": 2} for u in ranked[:limit]]


def retrieve(ov, base, query, filtered, limit=20):
    if filtered == "grep":
        return grep_recipe(ov, base, query, limit)
    if filtered == "agent":
        return agent_recipe(ov, base, query, limit)
    if filtered == "search":
        return search(ov, base, query, limit)
    if filtered == "files":
        return ov.find(query, target_uri=base, limit=limit, level=2).get("resources", [])
    if not filtered:
        return ov.find(query, target_uri=base, limit=limit).get("resources", [])
    out, seen = [], set()
    for tag in date_filters(query) + [None]:
        try:
            rs = ov.find(query, target_uri=base, limit=limit, tags=[tag]) if tag else \
                ov.find(query, target_uri=base, limit=limit, level=2)
        except OVError:
            rs = {}
        for r in rs.get("resources", []):
            if r["uri"] not in seen:
                seen.add(r["uri"])
                out.append(r)
    return out[:limit]


NOTION_ID = re.compile(r"notion_page_id:\s*['\"]?([0-9a-f-]{32,36})")


def doc_of(uri, text, base):
    """The source document a file belongs to: its frontmatter notion_page_id, else (v1
    parsed documents) the folder source/<type>/<doc> with any .body/.md suffix removed."""
    m = NOTION_ID.search(text[:2000])
    if m:
        return m.group(1).replace("-", "")
    parts = uri[len(base) + 1:].split("/")
    if len(parts) >= 3 and parts[0] == "source":
        return "/".join(parts[:2] + [parts[2].removesuffix(".body").removesuffix(".md")])
    return uri


def index_arm(files, base):
    files_norm = {u: norm(t) for u, t in files.items()}
    file_doc = {u: doc_of(u, t, base) for u, t in files.items()}
    doc_text = {}
    for u, d in file_doc.items():
        doc_text[d] = doc_text.get(d, "") + "\n" + files_norm[u]
    return {"files": files_norm, "file_doc": file_doc, "doc_text": doc_text}


def score_case(case, hits, idx):
    """First rank at which each quote is hit, per mode:
    strict  the level-2 file itself contains the quote;
    doc     a level-2 file of the document that contains it (the ledger's exact-source rule);
    lenient a doc hit, or a directory hit whose direct files contain it."""
    files = idx["files"]
    quotes = [norm(e["quote"]) for e in case["evidence"]]
    present = [any(q in t for t in files.values()) for q in quotes]
    first = {m: [None] * len(quotes) for m in ("strict", "doc", "lenient")}
    for rank, hit in enumerate(hits, 1):
        uri = hit["uri"]
        if hit.get("level") == 2 and uri in files:
            texts = {"strict": files[uri], "doc": idx["doc_text"][idx["file_doc"][uri]], "lenient": files[uri]}
        else:
            d = uri.rsplit("/", 1)[0] if uri.rsplit("/", 1)[-1].startswith(".") else uri
            texts = {"lenient": "\n".join(t for u, t in files.items() if u.rsplit("/", 1)[0] == d)}
        for mode, text in texts.items():
            for i, q in enumerate(quotes):
                if first[mode][i] is None and q in text:
                    first[mode][i] = rank
    for i in range(len(quotes)):  # strict implies doc, doc implies lenient
        for mode, weaker in (("doc", "strict"), ("lenient", "doc")):
            r = [x for x in (first[weaker][i], first[mode][i]) if x]
            first[mode][i] = min(r) if r else None
    return {"present": present, "first_rank": first}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", action="append", required=True, type=Path)
    ap.add_argument("--arm", action="append", required=True, help="name=viking://base[+filters]")
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--refresh-cache", action="store_true")
    args = ap.parse_args(argv)
    ov = OV(timeout=300)

    sets = {p.stem: json.loads(p.read_text())["cases"] for p in args.cases}
    report = {"arms": {}, "sets": {k: [c["case_id"] for c in v] for k, v in sets.items()}}
    for spec in args.arm:
        name, uri = spec.split("=", 1)
        mode = uri.rsplit("+", 1)[1] if "+" in uri else ""
        filtered = mode if mode in ("files", "search", "agent", "grep") else mode == "filters"
        base = uri.split("+", 1)[0].rstrip("/")
        files = cache_tree(ov, base, args.cache, args.refresh_cache)
        idx = index_arm(files, base)
        arm = {"base": base, "filtered": filtered, "files": len(files), "runs": []}
        for run in range(args.runs):
            cases = {}
            for set_name, cs in sets.items():
                for c in cs:
                    hits = retrieve(ov, base, c["query"], filtered)
                    cases[c["case_id"]] = {"set": set_name, "hits": [h["uri"] for h in hits],
                                           "levels": [h.get("level") for h in hits],
                                           **score_case(c, hits, idx)}
            arm["runs"].append(cases)
            print(f"{name} run {run + 1}: done", flush=True)
        report["arms"][name] = arm
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    summarize(report, sets)
    return 0


def summarize(report, sets):
    for set_name, cs in sets.items():
        n_ev = sum(len(c["evidence"]) for c in cs)
        print(f"\n== {set_name}: {len(cs)} cases, {n_ev} evidence quotes")
        for name, arm in report["arms"].items():
            for mode in ("strict", "doc", "lenient"):
                row = []
                for k in KS:
                    ev = [sum(1 for c in cs for r in run[c["case_id"]]["first_rank"][mode] if r and r <= k)
                          for run in arm["runs"]]
                    comp = [sum(1 for c in cs if all(r and r <= k for r in run[c["case_id"]]["first_rank"][mode]))
                            for run in arm["runs"]]
                    row.append(f"@{k} ev {min(ev)}-{max(ev)} comp {min(comp)}-{max(comp)}")
                present = sum(sum(arm["runs"][0][c["case_id"]]["present"]) for c in cs)
                print(f"  {name:<5} {mode:<7} present {present:>2}/{n_ev}  " + "  ".join(row))


if __name__ == "__main__":
    sys.exit(main())

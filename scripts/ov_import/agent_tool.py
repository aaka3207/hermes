#!/usr/bin/env python3
"""Read-only OpenViking tools for an eval agent, pinned to one tree, with a call log.

    python3 scripts/ov_import/agent_tool.py --run <run.json> search "query" [--limit 10]
    python3 scripts/ov_import/agent_tool.py --run <run.json> read <uri> [<uri> ...]
    python3 scripts/ov_import/agent_tool.py --run <run.json> grep "regex" [--uri <sub-uri>]
    python3 scripts/ov_import/agent_tool.py --run <run.json> list <uri>

<run.json> = {"root": "viking://...", "log": "<path>.jsonl"}. Mirrors vikingbot's tool
contract (openviking_search / multi_read / grep / list): search is the reranked `search`
endpoint and returns URIs with summaries; read returns whole files. Every URI must lie
under root. Each call is appended to the log, which is what eval scoring reads.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ovclient import OV, OVError  # noqa: E402

MAX_FILE_CHARS = 20000


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    r = sub.add_parser("read")
    r.add_argument("uris", nargs="+")
    g = sub.add_parser("grep")
    g.add_argument("pattern")
    g.add_argument("--uri")
    l = sub.add_parser("list")
    l.add_argument("uri")
    args = ap.parse_args(argv)
    run = json.loads(args.run.read_text())
    root = run["root"].rstrip("/")
    ov = OV(timeout=300)

    def inside(uri):
        if not (uri == root or uri.startswith(root + "/")):
            sys.exit(f"error: {uri} is outside {root}")
        return uri

    out, logged = [], {"t": round(time.time(), 1), "cmd": args.cmd}
    try:
        if args.cmd == "search":
            logged["query"] = args.query
            body = {"query": args.query, "target_uri": root, "limit": args.limit, "score_threshold": -1}
            res = ov._req("POST", "/api/v1/search/search", body=body).get("resources", [])
            logged["uris"] = [x["uri"] for x in res]
            for x in res:
                out.append(f"- {x['uri']}  (level {x.get('level')}, score {x.get('score', 0):.2f})\n"
                           f"  {(x.get('abstract') or '').strip()[:400]}")
        elif args.cmd == "read":
            logged["uris"] = [inside(u) for u in args.uris]
            for u in args.uris:
                try:
                    text = ov.read(u)
                    text = text if isinstance(text, str) else json.dumps(text)
                except OVError as e:
                    text = f"(error: {str(e)[:200]})"
                more = f"\n… [truncated at {MAX_FILE_CHARS} chars]" if len(text) > MAX_FILE_CHARS else ""
                out.append(f"===== {u}\n{text[:MAX_FILE_CHARS]}{more}")
        elif args.cmd == "grep":
            uri = inside(args.uri or root)
            logged.update(pattern=args.pattern, uri=uri)
            res = ov.grep(uri, args.pattern, case_insensitive=True, node_limit=60)
            matches = res.get("matches", []) if isinstance(res, dict) else []
            logged["uris"] = list(dict.fromkeys(m["uri"] for m in matches))
            out += [f"- {m['uri']}:{m.get('line')}: {m.get('content', '')[:200]}" for m in matches]
            if not matches:
                out.append("(no matches)")
        elif args.cmd == "list":
            uri = inside(args.uri)
            items = ov.ls(uri)
            logged.update(uri=uri, uris=[x["uri"] for x in items])
            for x in items:
                kind = "dir " if x.get("isDir") else "file"
                out.append(f"- [{kind}] {x['uri']}  {(x.get('abstract') or '').strip()[:200]}")
    finally:
        with open(run["log"], "a") as f:
            f.write(json.dumps(logged, ensure_ascii=False) + "\n")
    print("\n".join(out) if out else "(no results)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Snapshot a Notion hub to local JSON with the `ntn` CLI, for build.py.

    python3 scripts/ov_import/fetch.py --config ~/.local/share/ov-import/career/config.yaml

Writes <config dir>/snapshots/<UTC stamp>/ (or --out):
    pages/<page_id>.json   {"id", "kind", "database", "title", "page", "markdown",
                            "truncated", "unknown_block_ids"}
    index.json             one row per page plus the unconfigured children found
and points <config dir>/snapshots/latest at it.

Read-only against Notion. Needs `ntn login` first. Hub config and snapshots hold
private data: keep them outside the (public) repo.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

PAGE_LINK = re.compile(r'<page url="https://app\.notion\.com/p/([0-9a-f]{32})"')


def ntn(args, retries=8):
    """Run ntn and return parsed JSON. Retries rate limits and transient errors."""
    for attempt in range(retries + 1):
        proc = subprocess.run(["ntn", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        if attempt == retries:
            raise RuntimeError(f"ntn {' '.join(args)} failed: {proc.stderr.strip()[:500]}")
        wait = 10 * (attempt + 1) if "429" in proc.stderr else 2 ** attempt
        time.sleep(wait)


def plain_id(page_id):
    return page_id.replace("-", "")


def title_of(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"]).strip()
    return ""


def query_data_source(ds_id):
    pages, cursor = [], None
    while True:
        args = ["datasources", "query", ds_id, "--json", "--limit", "100"]
        if cursor:
            args += ["--start-cursor", cursor]
        data = ntn(args)
        pages += data["results"]
        if not data.get("has_more"):
            return pages
        cursor = data["next_cursor"]


def child_blocks(block_id):
    blocks, cursor = [], None
    while True:
        path = [f"v1/blocks/{block_id}/children", "page_size==100"]
        if cursor:
            path.append(f"start_cursor=={cursor}")
        data = ntn(["api", *path])
        blocks += data["results"]
        if not data.get("has_more"):
            return blocks
        cursor = data["next_cursor"]


def plan_pages(cfg):
    """Return (jobs, unconfigured). Each job: id, kind, database, query_page."""
    jobs = [{"id": plain_id(cfg["notion_root"]), "kind": "hub", "database": None, "query_page": None}]
    for name, db in cfg.get("databases", {}).items():
        skip = set(db.get("skip_titles", []))
        for page in query_data_source(db["data_source"]):
            if page.get("in_trash") or any(title_of(page).startswith(x) for x in skip):
                continue
            jobs.append({"id": plain_id(page["id"]), "kind": db["adapter"], "database": name,
                         "query_page": page})
    children = cfg.get("children", {})
    exclude = {plain_id(x) for x in children.get("exclude", [])}
    unconfigured = []
    for block in child_blocks(cfg["notion_root"]):
        pid = plain_id(block["id"])
        if block["type"] == "child_page":
            if pid in exclude:
                continue
            if children.get("include_all_child_pages"):
                jobs.append({"id": pid, "kind": "child", "database": None, "query_page": None})
            else:
                unconfigured.append({"id": pid, "title": block["child_page"]["title"]})
        elif block["type"] == "child_database" and pid not in exclude:
            if not any(plain_id(db.get("database", "")) == pid for db in cfg.get("databases", {}).values()):
                unconfigured.append({"id": pid, "title": block["child_database"]["title"], "type": "database"})
    for pid in children.get("transcripts", {}).get("pages", []):
        jobs.append({"id": plain_id(pid), "kind": "transcript", "database": None, "query_page": None})
    return jobs, unconfigured


def fetch_one(job, pages_dir):
    data = ntn(["pages", "get", job["id"], "--json"])
    md = data["markdown"]
    record = {
        "id": job["id"],
        "kind": job["kind"],
        "database": job["database"],
        "parent_id": job.get("parent_id"),
        "title": title_of(data["page"]),
        "page": data["page"],
        "query_page": job["query_page"],
        "markdown": md["markdown"],
        "truncated": md.get("truncated", False),
        "unknown_block_ids": md.get("unknown_block_ids", []),
    }
    (pages_dir / f"{job['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text())
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or args.config.parent / "snapshots" / stamp
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=False)

    jobs, unconfigured = plan_pages(cfg)
    with ThreadPoolExecutor(args.workers) as pool:
        records = list(pool.map(lambda j: fetch_one(j, pages_dir), jobs))
        # Follow sub-pages (<page> blocks) of fetched child pages, depth-first until none are new.
        exclude = {plain_id(x) for x in cfg.get("children", {}).get("exclude", [])}
        frontier = [r for r in records if r["kind"] in ("child", "transcript")]
        while frontier:
            known = {r["id"] for r in records} | exclude
            sub = []
            for r in frontier:
                for pid in dict.fromkeys(PAGE_LINK.findall(r["markdown"])):
                    if pid not in known:
                        known.add(pid)
                        sub.append({"id": pid, "kind": r["kind"], "database": None, "query_page": None,
                                    "parent_id": r["id"]})
            frontier = list(pool.map(lambda j: fetch_one(j, pages_dir), sub))
            records += frontier

    known = {r["id"] for r in records}
    rows = []
    for r in records:
        links = sorted(set(PAGE_LINK.findall(r["markdown"])) - known)
        rows.append({"id": r["id"], "kind": r["kind"], "database": r["database"], "title": r["title"],
                     "parent_id": r.get("parent_id"),
                     "last_edited_time": r["page"]["last_edited_time"], "chars": len(r["markdown"]),
                     "truncated": r["truncated"], "unknown_blocks": len(r["unknown_block_ids"]),
                     "unfetched_page_links": links})
    index = {"snapshot_utc": stamp, "config": str(args.config), "pages": rows, "unconfigured": unconfigured}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    latest = out.parent / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(out.name)

    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    print(f"snapshot {out}")
    for kind, items in sorted(by_kind.items()):
        print(f"  {kind:<11} {len(items):>3} pages {sum(i['chars'] for i in items):>9,} chars")
    for r in rows:
        if r["truncated"] or r["unknown_blocks"]:
            print(f"  WARN {r['id']} {r['title'][:50]!r}: truncated={r['truncated']} unknown_blocks={r['unknown_blocks']}")
        if r["unfetched_page_links"]:
            print(f"  NOTE {r['title'][:50]!r} links to unfetched pages: {r['unfetched_page_links']}")
    for u in unconfigured:
        print(f"  UNCONFIGURED {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

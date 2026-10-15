#!/usr/bin/env python3
"""Verify an uploaded tree against its build: names, bytes and tags.

    python3 scripts/ov_import/verify.py --config ~/.local/share/ov-import/career/config.yaml \
        [--target viking://...] [--group source/changelog/2026-04 ...]

For each group: every build file must exist at the same relative path, with identical
bytes, and carry at least its tags.json tags; remote files absent from the build are
listed (for a fresh import there should be none; after cutover, dual-writes appear here).
Exit 1 on any mismatch.

A file that is stored but has no vector record shows up as untagged even though set_tags
reported success (it matched no records; seen on 22 of 397 files on 2026-10-15). --repair
rewrites each untagged file with its build bytes and tags via content/write, which
re-embeds it, then re-checks.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ovclient import OV, OVError  # noqa: E402
from upload import groups_of  # noqa: E402


def remote_files(ov, uri):
    try:
        items = ov.ls(uri, recursive=True)
    except OVError as e:
        return None, str(e)
    return {x["uri"][len(uri) + 1:]: x for x in items if not x["isDir"]}, None


def tags_of(ov, uri):
    """Tags live on vector records; glob returns them per file."""
    res = ov._req("POST", "/api/v1/search/glob", body={"pattern": "**/*", "uri": uri, "node_limit": 10000, "include_tags": True})
    return {m["uri"]: set(m.get("tags") or []) for m in res.get("matches", [])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--build", type=Path)
    ap.add_argument("--target")
    ap.add_argument("--group", action="append")
    ap.add_argument("--repair", action="store_true", help="rewrite untagged files (same bytes + tags) to re-embed them")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    build = (args.build or args.config.parent / "builds" / "latest").resolve()
    tree = build / "tree"
    tags = json.loads((build / "tags.json").read_text())
    target = (args.target or cfg["target_uri"]).rstrip("/")
    ov = OV(timeout=300)

    bad = 0
    for group in args.group or groups_of(tree):
        uri = f"{target}/{group}"
        local = sorted(p[len(group) + 1:] for p in tags if p.startswith(group + "/"))
        remote, err = remote_files(ov, uri)
        if remote is None:
            print(f"{group}: MISSING ({err[:120]})")
            bad += 1
            continue
        missing = [p for p in local if p not in remote]
        extra = sorted(p for p in remote if p not in set(local))

        def compare(rel):
            data = ov.download(f"{uri}/{rel}")
            return rel if data != (tree / group / rel).read_bytes() else None

        with ThreadPoolExecutor(6) as pool:
            differ = [r for r in pool.map(compare, [p for p in local if p in remote]) if r]
        remote_tags = tags_of(ov, uri)
        untagged = [p for p in local if p in remote and not set(tags[f"{group}/{p}"]) <= remote_tags.get(f"{uri}/{p}", set())]
        if untagged and args.repair:
            # wait=False: a waiting write can outlast Cloudflare's 100 s limit (524). Poll tags instead.
            for p in untagged:
                ov.write(f"{uri}/{p}", (tree / group / p).read_text(), mode="replace",
                         tags=tags[f"{group}/{p}"], tag_mode="replace", wait=False)
            deadline = time.time() + 1800
            while untagged and time.time() < deadline:
                time.sleep(30)
                remote_tags = tags_of(ov, uri)
                untagged = [p for p in untagged if not set(tags[f"{group}/{p}"]) <= remote_tags.get(f"{uri}/{p}", set())]
        ok = not (missing or differ or untagged)
        bad += 0 if ok else 1
        print(f"{group:<34} local {len(local):>4} remote {len(remote):>4}  missing {len(missing)} "
              f"differ {len(differ)} untagged {len(untagged)} extra {len(extra)}  {'OK' if ok else 'FAIL'}")
        for label, items in (("missing", missing), ("differ", differ), ("untagged", untagged), ("extra", extra)):
            for p in items[:5]:
                print(f"    {label}: {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

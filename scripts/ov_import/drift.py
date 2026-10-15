#!/usr/bin/env python3
"""Drift sample: compare N random Notion pages with the uploaded tree.

    python3 scripts/ov_import/drift.py --config ~/.local/share/ov-import/career/config.yaml \
        [--n 20] [--seed 20261014] [--target viking://...]

For each sampled page (seeded, from the snapshot index):
  loss   lines of the snapshot body (cleaned as build.py does) absent from the text the
         server returns for the files carrying that page's notion id. Should be 0, apart
         from lines a changelog split plan drops on purpose;
  drift  lines added or removed in Notion since the snapshot (fresh read-only fetch with
         ntn) — what a catch-up would have to apply.
Exit 1 when any page shows loss.
"""
import argparse
import json
import random
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from build import WRAPPER_LINES, apply_offsets, clean, load_plans, strip_frontmatter  # noqa: E402
from check_build import norm  # noqa: E402
from fetch import ntn  # noqa: E402
from ovclient import OV  # noqa: E402


def lines_of(md):
    return [norm(l) for l in clean(strip_frontmatter(md).strip()).split("\n")
            if l.strip() and l.strip() not in WRAPPER_LINES]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--target")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261014)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    base = args.config.parent
    build_dir = (base / "builds" / "latest").resolve()
    snapshot = base / "snapshots" / build_dir.name
    target = (args.target or cfg["target_uri"]).rstrip("/")
    manifest = json.loads((build_dir / "manifest.json").read_text())
    plans = load_plans(base / "changelog_splits")
    skip = tuple(cfg["databases"]["changelog"].get("skip_titles", []))
    ov = OV(timeout=300)

    paths = {}
    for m in manifest:
        if m["notion_id"]:
            paths.setdefault(m["notion_id"], []).append(m["path"])
    rows = [r for r in json.loads((snapshot / "index.json").read_text())["pages"] if r["id"] in paths]
    sample = random.Random(args.seed).sample(rows, min(args.n, len(rows)))

    lossy = 0
    for row in sample:
        rec = json.loads((snapshot / "pages" / f"{row['id']}.json").read_text())
        if rec["kind"] == "changelog" and skip and rec["title"].startswith(skip):
            continue
        body = strip_frontmatter(rec["markdown"]).strip()
        if rec["kind"] == "changelog" and rec["id"] in plans:
            body = apply_offsets(body, plans[rec["id"]])
        snap = lines_of(body)
        remote = set()
        for p in paths[rec["id"]]:
            remote.update(norm(l) for l in ov.download(f"{target}/{p}").decode().split("\n") if l.strip())
        loss = [l for l in snap if l not in remote and not any(len(h) > 40 and l.startswith(h) for h in remote)]

        fresh_doc = ntn(["pages", "get", rec["id"], "--json"])
        fresh = lines_of(fresh_doc["markdown"]["markdown"])
        snap_raw = set(lines_of(strip_frontmatter(rec["markdown"])))
        added = [l for l in fresh if l not in snap_raw]
        removed = [l for l in snap_raw if l not in set(fresh)]
        edited = fresh_doc["page"].get("last_edited_time") != rec["page"].get("last_edited_time")
        lossy += bool(loss)
        print(f"{rec['kind']:<10} {rec['title'][:44]:<44} files {len(paths[rec['id']]):>2}  lines {len(snap):>4}  "
              f"loss {len(loss):>2}  drift +{len(added)}/-{len(removed)}{'  (edited since snapshot)' if edited else ''}")
        for l in loss[:2]:
            print(f"    loss: {l[:100]!r}")
    print(f"\n{len(sample)} pages sampled, {lossy} with loss")
    return 1 if lossy else 0


if __name__ == "__main__":
    sys.exit(main())

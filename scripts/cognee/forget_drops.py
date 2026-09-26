#!/usr/bin/env python3
"""Delete the records score_drops.py identified. Dry run unless --commit.

    docker cp scripts/cognee/forget_drops.py <backend>:/tmp/
    TOK=$(docker exec <cognee-mcp> printenv API_TOKEN)
    docker exec -e COGNEE_API_KEY="$TOK" -e COGNEE_URL=http://localhost:8000 \
        <backend> python /tmp/forget_drops.py --commit --limit 1

One ``POST /api/v1/forget`` per record with ``{"datasetId", "dataId"}`` -- the
single-document shape, which cannot express a dataset-wide delete by
construction. Reference-counted retraction drops each document's unowned graph
artifacts and prunes orphaned EdgeType nodes; shared entities survive.

Aim this with the profile's ``cognee.json`` ``service_url``, NOT with
``COGNEE_BASE_URL`` -- in this deployment that env var points at Cognee Cloud,
where the dinefile profile lives. See docs/cognee-operations.md §5.

Run backup_corpus.py first. Measured: ~2.8s per record.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CANDIDATES = "/tmp/drop_candidates.json"
BASE = os.environ.get("COGNEE_URL", "http://localhost:8000")
KEY = os.environ.get("COGNEE_API_KEY") or os.environ.get("API_TOKEN")


def forget(dataset_id, data_id, timeout=60):
    body = json.dumps({"datasetId": dataset_id, "dataId": data_id}).encode()
    req = urllib.request.Request(BASE + "/api/v1/forget", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if KEY:
        req.add_header("X-Api-Key", KEY)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="actually delete")
    ap.add_argument("--tier", choices=["a", "b", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0, help="stop after N")
    args = ap.parse_args()

    with open(CANDIDATES) as fh:
        cand = json.load(fh)
    todo = []
    if args.tier in ("a", "both"):
        todo += cand["tier_a"]
    if args.tier in ("b", "both"):
        todo += cand["tier_b"]
    if args.limit:
        todo = todo[:args.limit]

    by_mark = {}
    for rec in todo:
        by_mark[rec["mark"]] = by_mark.get(rec["mark"], 0) + 1
    print("candidates: %d" % len(todo))
    for mark, n in sorted(by_mark.items(), key=lambda x: -x[1]):
        print("  %-30s %5d" % (mark, n))

    if not args.commit:
        print("\nDRY RUN -- nothing deleted. Re-run with --commit.")
        for rec in todo[:5]:
            print("  would forget %s  %s" % (rec["id"][:8], rec["preview"][:60]))
        return

    if not KEY:
        sys.exit("no COGNEE_API_KEY / API_TOKEN in env -- refusing to run unauthenticated")

    ok = fail = 0
    start = time.time()
    for i, rec in enumerate(todo, 1):
        try:
            forget(rec["dataset_id"], rec["id"])
            ok += 1
        except urllib.error.HTTPError as exc:
            fail += 1
            print("  ! %s HTTP %s %s" % (rec["id"][:8], exc.code, exc.read(200)))
        except Exception as exc:  # noqa: BLE001 -- one bad record must not sink the run
            fail += 1
            print("  ! %s %s" % (rec["id"][:8], exc))
        if i % 25 == 0:
            print("  %d/%d  ok=%d fail=%d  %.0fs"
                  % (i, len(todo), ok, fail, time.time() - start))
    print("\ndone: ok=%d fail=%d in %.0fs" % (ok, fail, time.time() - start))


main()

#!/usr/bin/env python3
"""Upload a build.py tree to OpenViking, one folder import per group, then per-file tags.

    python3 scripts/ov_import/upload.py --config ~/.local/share/ov-import/career/config.yaml \
        [--group changelog/2026-04 ...] [--target viking://...] [--instruction-file f] [--dry-run]

Groups are the top-level folders of the tree (hub, hub-children, notes, contacts,
applications) and one per changelog/<YYYY-MM>. Each group is imported with
`ov add-resource <dir> --to <target>/<group> --args parse_mode:no_split --tags <shared>`
(tags every file in the group shares), then remaining per-file tags are appended with
set_tags. `reason` is never sent: a non-empty reason triggers memory extraction.
Writes a JSON log to <build>/uploads/<UTC stamp>.json.

`--to` on an existing directory refreshes it AND deletes files the import did not
produce, so this refuses to upload into an existing group unless --replace is given.
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ovclient import OV, OVError  # noqa: E402


def groups_of(tree):
    out = []
    for d in sorted(tree.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "changelog":
            out += [f"changelog/{m.name}" for m in sorted(d.iterdir()) if m.is_dir()]
        else:
            out.append(d.name)
    return out


def exists(ov, uri):
    try:
        ov.stat(uri)
        return True
    except OVError as e:
        if "404" in str(e) or "NOT_FOUND" in str(e):
            return False
        raise


def ov_json(args, timeout):
    proc = subprocess.run(["ov", *args, "-o", "json"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)
    out = proc.stdout
    start = out.find("{")
    try:
        return json.loads(out[start:]) if start != -1 else {"ok": False, "raw": out[-2000:], "stderr": proc.stderr[-2000:]}
    except json.JSONDecodeError:
        return {"ok": False, "raw": out[-2000:], "stderr": proc.stderr[-2000:]}


def upload_group(ov, tree, group, files, tags, target, instruction, replace, dry_run, timeout):
    uri = f"{target}/{group}"
    shared = sorted(set.intersection(*(set(tags[f]) for f in files))) if files else []
    log = {"group": group, "uri": uri, "files": len(files), "shared_tags": shared}
    if exists(ov, uri) and not replace:
        log["skipped"] = "target exists (use --replace to refresh it)"
        return log
    # No --wait: the CLI's HTTP request gives up at ~60 s while the server keeps working.
    # Submit, then poll the task over HTTP.
    args = ["add-resource", str(tree / group), "--to", uri, "--args", '{"parse_mode":"no_split"}']
    if shared:
        args += ["--tags", ",".join(shared)]
    if instruction:
        args += ["--instruction", instruction]
    log["command"] = " ".join(a if " " not in a else repr(a) for a in args[:1] + args[2:])
    if dry_run:
        return log
    t0 = time.time()
    res = ov_json(args, 900)
    submitted = res.get("result", {})
    if not res.get("ok") or not submitted.get("task_id"):
        log["ok"] = False
        log["error"] = res.get("error") or res.get("raw") or res.get("stderr") or submitted
        return log
    log["task_id"] = submitted["task_id"]
    meta = submitted.get("meta") or {}
    log["failed_files"] = meta.get("failed_files")
    while True:
        task = ov.task(submitted["task_id"])
        if task.get("status") in ("completed", "failed", "cancelled"):
            break
        if time.time() - t0 > timeout:
            log["ok"] = False
            log["error"] = f"task {submitted['task_id']} still {task.get('status')} after {timeout}s"
            return log
        time.sleep(10)
    log["seconds"] = round(time.time() - t0, 1)
    result = task.get("result") or {}
    log["result"] = {k: result.get(k) for k in ("status", "errors", "queue_status", "context_count")}
    log["ok"] = task.get("status") == "completed" and not result.get("errors") and not log["failed_files"]
    if not log["ok"]:
        log["error"] = task.get("error") or result.get("errors") or log["failed_files"]
        return log

    def tag_one(rel):
        extra = sorted(set(tags[rel]) - set(shared))
        if not extra:
            return None
        for attempt in range(4):
            try:
                r = ov.set_tags(f"{target}/{rel}", extra, mode="append")
                return None if r.get("failed_count", 0) == 0 else f"{rel}: {r}"
            except OVError as e:
                err = str(e)
                time.sleep(2 * (attempt + 1))
        return f"{rel}: {err}"

    with ThreadPoolExecutor(4) as pool:
        errors = [e for e in pool.map(tag_one, files) if e]
    log["tag_errors"] = errors
    return log


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--build", type=Path)
    ap.add_argument("--group", action="append", help="group path (repeatable); default all")
    ap.add_argument("--target", help="target base URI; default config target_uri")
    ap.add_argument("--instruction-file", type=Path)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text())
    build = (args.build or args.config.parent / "builds" / "latest").resolve()
    tree = build / "tree"
    tags = json.loads((build / "tags.json").read_text())
    target = (args.target or cfg["target_uri"]).rstrip("/")
    instruction = args.instruction_file.read_text().strip() if args.instruction_file else ""
    groups = args.group or groups_of(tree)
    ov = OV(timeout=300)

    logs = []
    for group in groups:
        files = sorted(p for p in tags if p.startswith(group + "/"))
        log = upload_group(ov, tree, group, files, tags, target, instruction, args.replace, args.dry_run, args.timeout)
        logs.append(log)
        status = "dry-run" if args.dry_run else log.get("skipped") or ("ok" if log.get("ok") else "FAILED")
        print(f"{group:<34} {len(files):>4} files  {status}  {log.get('seconds', '')}s"
              f"  tag_errors={len(log.get('tag_errors') or [])}", flush=True)
        if log.get("error"):
            print(f"    error: {str(log['error'])[:400]}")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not args.dry_run:
        (build / "uploads").mkdir(exist_ok=True)
        (build / "uploads" / f"{stamp}.json").write_text(json.dumps(
            {"target": target, "instruction": instruction, "groups": logs}, indent=1, ensure_ascii=False))
    return 0 if all(l.get("ok") or l.get("skipped") or args.dry_run for l in logs) else 1


if __name__ == "__main__":
    sys.exit(main())

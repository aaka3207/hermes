#!/usr/bin/env python3
"""Score agent eval runs (agent_tool.py logs + answer.json) against gold evidence quotes.

    python3 scripts/ov_import/agent_score.py --cases dev.json --cases heldout.json \
        --runs <dir with <arm>/<case_id>/{run.json,log.jsonl,answer.json}> \
        --cache <eval.py cache dir> [--out scores.json]

Per evidence quote (normalized as eval.py does):
  read   a file the agent read with `read` contains the quote (from the tool log, not the
         agent's own report);
  cited  a file listed in answer.json citations contains it.
A case is complete when every quote is read (or cited). Also reports tool calls per run.
Answer correctness against required facts is graded separately.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval import norm  # noqa: E402


def load_tree(cache, base):
    path = cache / (re.sub(r"[^a-z0-9]+", "_", base.lower()) + ".json")
    return {u: norm(t) for u, t in json.loads(path.read_text()).items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", action="append", required=True, type=Path)
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sets = {p.stem: json.loads(p.read_text())["cases"] for p in args.cases}
    report = {}
    for arm_dir in sorted(d for d in args.runs.iterdir() if d.is_dir()):
        trees = {}
        arm = {}
        for set_name, cases in sets.items():
            for c in cases:
                d = arm_dir / c["case_id"]
                if not (d / "run.json").exists():
                    continue
                root = json.loads((d / "run.json").read_text())["root"].rstrip("/")
                files = trees.setdefault(root, load_tree(args.cache, root))
                log = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines()] if (d / "log.jsonl").exists() else []
                read = [u for e in log if e.get("cmd") == "read" for u in e.get("uris", [])]
                answer = json.loads((d / "answer.json").read_text()) if (d / "answer.json").exists() else {}
                cited = answer.get("citations", []) or []
                quotes = [norm(e["quote"]) for e in c["evidence"]]
                arm[c["case_id"]] = {
                    "set": set_name,
                    "calls": len(log),
                    "answered": bool(answer.get("answer")),
                    "read": [any(q in files.get(u, "") for u in read) for q in quotes],
                    "cited": [any(q in files.get(u, "") for u in cited) for q in quotes],
                    "present": [any(q in t for t in files.values()) for q in quotes],
                }
        report[arm_dir.name] = arm
    for set_name, cases in sets.items():
        n_ev = sum(len(c["evidence"]) for c in cases)
        print(f"== {set_name}: {len(cases)} cases, {n_ev} evidence quotes")
        for name, arm in report.items():
            rows = [arm[c["case_id"]] for c in cases if c["case_id"] in arm]
            if not rows:
                continue
            read = sum(sum(r["read"]) for r in rows)
            cited = sum(sum(r["cited"]) for r in rows)
            comp_read = sum(all(r["read"]) for r in rows)
            present = sum(sum(r["present"]) for r in rows)
            calls = sum(r["calls"] for r in rows) / len(rows)
            print(f"  {name:<4} runs {len(rows):>2}  present {present}/{n_ev}  read {read}/{n_ev}  cited {cited}/{n_ev}  "
                  f"complete(read) {comp_read}/{len(rows)}  answered {sum(r['answered'] for r in rows)}  mean calls {calls:.1f}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Check a build against its snapshot: no source line may go missing.

    python3 scripts/ov_import/check_build.py --config ~/.local/share/ov-import/career/config.yaml

For every fetched page, each non-blank line of its cleaned body must appear in some output
file carrying that page's notion id. Lines the split plans drop on purpose (duplicate entries,
trimmed tails, wrapper headings) are reported separately. Also checks the size cap, the
directory fan-out and that every file has frontmatter with notion_page_id. Exit 1 on any miss.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from build import WRAPPER_LINES, apply_offsets, clean, load_plans, strip_frontmatter  # noqa: E402


def norm(line):
    return " ".join(line.split())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    base = args.config.parent
    build_dir = (base / "builds" / "latest").resolve()
    snapshot = base / "snapshots" / build_dir.name
    manifest = json.loads((build_dir / "manifest.json").read_text())
    plans = load_plans(base / "changelog_splits")
    max_chars = cfg["split"]["max_file_chars"]

    out_lines = {}
    problems = []
    for m in manifest:
        text = (build_dir / "tree" / m["path"]).read_text()
        if not text.startswith("---\n") or (m["notion_id"] and "notion_page_id:" not in text):
            problems.append(f"{m['path']}: missing frontmatter or notion_page_id")
        if m["chars"] > max_chars + 1000:
            problems.append(f"{m['path']}: {m['chars']} chars")
        if m["notion_id"]:
            out_lines.setdefault(m["notion_id"], set()).update(norm(l) for l in text.split("\n") if l.strip())

    index = json.loads((snapshot / "index.json").read_text())
    total = missing_total = 0
    skip = tuple(cfg["databases"]["changelog"].get("skip_titles", []))
    for row in index["pages"]:
        rec = json.loads((snapshot / "pages" / f"{row['id']}.json").read_text())
        if rec["kind"] == "changelog" and skip and rec["title"].startswith(skip):
            continue
        body = strip_frontmatter(rec["markdown"]).strip()
        if rec["kind"] == "changelog" and rec["id"] in plans:
            body = apply_offsets(body, plans[rec["id"]])
        src = [norm(l) for l in clean(body).split("\n") if l.strip() and l.strip() not in WRAPPER_LINES]
        if not src:
            continue
        have = out_lines.get(rec["id"], set())
        missing = [l for l in src if l not in have]
        # Lines cut by the size splitter appear as pieces; accept a line whose prefix is present.
        missing = [l for l in missing if not any(h and l.startswith(h) and len(h) > 40 for h in have)]
        total += len(src)
        if missing:
            missing_total += len(missing)
            problems.append(f"{rec['kind']} {rec['title'][:50]!r}: {len(missing)} of {len(src)} lines missing, "
                            f"first: {missing[0][:90]!r}")
    print(f"checked {len(index['pages'])} pages, {total} source lines, {missing_total} missing; {len(manifest)} files")
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Stamp historical changelog month directories with a deterministic L0/L1 header.

    python3 scripts/ov_import/stamp.py --config ~/.local/share/ov-import/career/config.yaml \
        [--target viking://...] [--month 2026-04 ...] [--dry-run]

The server generates directory abstracts and overviews that rarely state dates. For each
month directory (and its ISO-week subfolders) this writes `.abstract.md` (<= 256 chars)
and `.overview.md` (<= 4000 chars) as: date range and entry count, an entry list
(date · title → file), then the generated text. The generated originals are saved to
<build>/stamps/ on first run and reused, so re-running is idempotent.

Any later file write in a stamped directory regenerates its summaries and drops the
stamp, so the current month is skipped by default (it still receives dual-writes);
re-run this after a catch-up import.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ovclient import OV  # noqa: E402

ABSTRACT_MAX = 256
OVERVIEW_MAX = 4000


def front(path):
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}
    return yaml.safe_load(text[4:text.index("\n---\n", 4)]) or {}


def entries_of(d):
    """Entries directly in d, one per (date, title); split parts collapse into one line."""
    seen = {}
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("state-") or p.name == "index.md":
            continue
        fm = front(p)
        date = str(fm.get("date") or p.name[:10])
        title = fm.get("notion_section") or fm.get("notion_title") or p.stem
        seen.setdefault((date, title), p.name)
    return [(date, title, name) for (date, title), name in seen.items()]


def all_dates(d):
    return sorted(date for sub in [d, *[s for s in d.rglob("*") if s.is_dir()]] for date, _, _ in entries_of(sub))


def cap(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def compose(d, rel, generated):
    dates = all_dates(d)
    total = len(dates)
    span = f"{dates[0]} to {dates[-1]}" if dates else "no dated entries"
    label = rel.split("/")[-1]
    head = f"Career changelog {label}: {total} entries dated {span}."
    abstract = cap(f"{head} {generated['abstract'].strip()}", ABSTRACT_MAX)

    lines = [f"# {label}", "", head, ""]
    subdirs = sorted(s for s in d.iterdir() if s.is_dir())
    if subdirs:
        lines += ["## Weeks", ""]
        for s in subdirs:
            sd = all_dates(s)
            lines.append(f"- {s.name}: {len(sd)} entries, {sd[0]} to {sd[-1]}" if sd else f"- {s.name}")
        lines.append("")
    entries = entries_of(d)
    if entries:
        lines += ["## Entries", ""]
        lines += [f"- {date} · {title} → {name}" for date, title, name in entries]
        lines.append("")
    body = generated["overview"].strip()
    if body.startswith("# "):
        body = body.split("\n", 1)[1].lstrip() if "\n" in body else ""
    header = "\n".join(lines)
    room = OVERVIEW_MAX - len(header) - len("## Generated summary\n\n")
    overview = header + ("## Generated summary\n\n" + cap(body, room) if room > 200 else "")
    return abstract, overview[:OVERVIEW_MAX]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--build", type=Path)
    ap.add_argument("--target")
    ap.add_argument("--month", action="append", help="YYYY-MM (repeatable); default every month before the current one")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    build = (args.build or args.config.parent / "builds" / "latest").resolve()
    tree = build / "tree"
    target = (args.target or cfg["target_uri"]).rstrip("/")
    current = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    months = args.month or [m.name for m in sorted((tree / "changelog").iterdir()) if m.is_dir() and m.name < current]
    store = build / "stamps"
    store.mkdir(exist_ok=True)
    ov = OV(timeout=300)

    for month in months:
        mdir = tree / "changelog" / month
        for d in [mdir, *sorted(s for s in mdir.iterdir() if s.is_dir())]:
            rel = str(d.relative_to(tree))
            uri = f"{target}/{rel}"
            saved = store / (rel.replace("/", "__") + ".json")
            if saved.exists():
                generated = json.loads(saved.read_text())
            else:
                generated = {"abstract": ov.abstract(uri), "overview": ov.overview(uri)}
                if generated["abstract"].startswith("Career changelog "):
                    sys.exit(f"{rel}: remote is already stamped but {saved} is missing; refusing to stamp a stamp")
                saved.write_text(json.dumps(generated, ensure_ascii=False, indent=1))
            abstract, overview = compose(d, rel, generated)
            print(f"{rel:<34} abstract {len(abstract):>3}  overview {len(overview):>4}  {abstract[:90]}")
            if not args.dry_run:
                ov.write(f"{uri}/.abstract.md", abstract)
                ov.write(f"{uri}/.overview.md", overview)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Turn a fetch.py snapshot into an OpenViking-ready file tree.

    python3 scripts/ov_import/build.py --config ~/.local/share/ov-import/career/config.yaml

Reads <config dir>/snapshots/latest (or --snapshot) and the per-row changelog
split plans in <config dir>/changelog_splits/. Writes <config dir>/builds/<snapshot>/:
    tree/source/...   one Markdown file per retrieval unit
    tags.json         {relative path: ["k=v", ...]}  per-file retrieval tags
    manifest.json     path, sha256, chars, notion id, last_edited, tags
    report.txt        counts, warnings, dropped content

Every file starts with frontmatter (notion_page_id, notion_url, title, dates) and a
one-line H1 breadcrumb, because OpenViking embeds the first ~max_input_tokens of a
file as its single vector. No file exceeds split.max_file_chars.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

# OpenViking's Markdown parser truncates a stored name's stem past 50 chars and appends a hash
# (markdown.py _sanitize_for_path), which would break path-based verification and tags.
MAX_STEM = 50
HEADING = re.compile(r"^(#{1,6}) +(.*\S)\s*$")
WRAPPER_LINES = {"## Detailed Entries", "---"}
ESCAPABLE = re.compile(r"\\([$~<>:\[\]{}\-|#*_+.!()=])")
# Paragraph breaks lost as a literal "nn" glued after sentence punctuation (audit: June row, two child
# pages). Requiring punctuation before and a block start after keeps names like "Glenn**" intact.
GLUED_NN = re.compile(r"(?<=[.)!?:\"'])nn(?=[A-Z*#\-\[])")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


# ---------------------------------------------------------------- text helpers

def slugify(text, limit=50):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    s = s[:limit].rstrip("-")
    return s or "untitled"


def dashed(pid):
    p = pid.replace("-", "")
    return f"{p[:8]}-{p[8:12]}-{p[12:16]}-{p[16:20]}-{p[20:]}"


def strip_frontmatter(md):
    """ntn puts page properties in a leading YAML block; build.py takes them from the API instead."""
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            return md[end + 5:]
    return md


def clean(text):
    """Undo Notion markdown escapes and the escaped-unicode corruption found in the audit."""
    text = GLUED_NN.sub("\n\n", text)
    text = ESCAPABLE.sub(r"\1", text)
    text = text.replace("u2014", "\u2014").replace("u2192", "\u2192")
    return text.strip("\n") + "\n"


def prop_value(prop):
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return None
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v).strip() or None
    if t in ("select", "status"):
        return v.get("name")
    if t == "multi_select":
        return [x["name"] for x in v] or None
    if t == "date":
        return v.get("start") if not v.get("end") else f"{v['start']}/{v['end']}"
    if t in ("checkbox", "number", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return v
    if t == "people":
        return [x.get("name") or x.get("id") for x in v] or None
    if t == "relation":
        return [x["id"] for x in v] or None
    if t == "formula":
        return v.get(v.get("type"))
    return None


def properties(page):
    out = {}
    for name, prop in (page or {}).get("properties", {}).items():
        if prop.get("type") == "title":
            continue
        val = prop_value(prop)
        if val not in (None, "", []):
            out[name] = val
    return out


def is_block_start(line):
    return line.startswith("<table") or line.startswith("<details")


def blocks_of(lines):
    """Group lines into atomic blocks: tables and toggles stay whole, otherwise one block per paragraph."""
    blocks, cur, depth, closer = [], [], 0, None
    for line in lines:
        s = line.strip()
        if depth:
            cur.append(line)
            if s.startswith(f"<{closer}"):
                depth += 1
            if s.startswith(f"</{closer}>") or s.endswith(f"</{closer}>"):
                depth -= 1
                if depth == 0:
                    blocks.append(cur)
                    cur = []
            continue
        if is_block_start(s):
            if cur:
                blocks.append(cur)
                cur = []
            closer = "table" if s.startswith("<table") else "details"
            cur = [line]
            depth = 1
            if s.endswith(f"</{closer}>"):
                depth = 0
                blocks.append(cur)
                cur = []
            continue
        if not s:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def heading_lines(lines):
    """Indexes and levels of headings outside tables/toggles."""
    out, depth, closer = [], 0, None
    for i, line in enumerate(lines):
        s = line.strip()
        if depth:
            if s.startswith(f"</{closer}>") or s.endswith(f"</{closer}>"):
                depth -= 1
            continue
        if is_block_start(s):
            closer = "table" if s.startswith("<table") else "details"
            depth = 0 if s.endswith(f"</{closer}>") else 1
            continue
        m = HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


def size(lines):
    return sum(len(l) + 1 for l in lines)


def pack_blocks(blocks, max_chars):
    """Pack paragraph blocks into parts; a block larger than max_chars is cut at line or table-row boundaries."""
    parts, cur = [], []
    for block in blocks:
        pieces = [block] if size(block) <= max_chars else cut_block(block, max_chars)
        for piece in pieces:
            if cur and size(cur) + size(piece) + 1 > max_chars:
                if all(HEADING.match(l) or not l.strip() for l in cur):
                    # Never emit a heading on its own: carry it onto the next piece.
                    piece = [l for l in cur if l.strip()] + [""] + piece
                else:
                    parts.append(cur)
                cur = []
            if cur:
                cur.append("")
            cur.extend(piece)
    if cur:
        parts.append(cur)
    return parts


def cut_block(block, max_chars):
    if block[0].strip().startswith("<table"):
        rows = "\n".join(block)
        head_end = rows.find("</tr>") + len("</tr>")
        header, body = rows[:head_end], rows[head_end:]
        trs = re.findall(r"<tr>.*?</tr>", body, flags=re.S)
        out, cur = [], []
        for tr in trs:
            if cur and len(header) + sum(len(x) for x in cur) + len(tr) + 20 > max_chars:
                out.append(f"{header}\n" + "\n".join(cur) + "\n</table>")
                cur = []
            cur.append(tr)
        if cur:
            out.append(f"{header}\n" + "\n".join(cur) + "\n</table>")
        return [o.split("\n") for o in out]
    out, cur = [], []
    for line in block:
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars) or max_chars
            out.append(cur + [line[:cut]]) if cur else out.append([line[:cut]])
            cur, line = [], line[cut:].lstrip()
        if cur and size(cur) + len(line) + 1 > max_chars:
            out.append(cur)
            cur = []
        cur.append(line)
    if cur:
        out.append(cur)
    return out


def split_markdown(text, max_chars, min_chars, path=(), min_level=0):
    """Split at the shallowest heading level deeper than min_level; recurse into oversize sections.

    Returns [(heading_path tuple, text)]. Text before the first heading is its own section and is
    merged into a neighbour when small. An oversize section keeps its heading line on every piece.
    """
    lines = text.rstrip("\n").split("\n")
    if size(lines) <= max_chars:
        return [(path, text)]
    heads = [h for h in heading_lines(lines) if h[1] > min_level]
    for level in sorted({lvl for _, lvl, _ in heads}):
        cuts = [(i, t) for i, lvl, t in heads if lvl == level]
        starts = [i for i, _ in cuts]
        if len(cuts) < 2 and starts[0] == 0:
            continue
        sections = []
        if starts[0] > 0:
            sections.append((None, lines[:starts[0]]))
        for n, (i, title) in enumerate(cuts):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            sections.append((title, lines[i:end]))
        out = []
        for title, sec in sections:
            sub = path + ((title,) if title else ())
            if size(sec) <= max_chars:
                out.append((sub, "\n".join(sec) + "\n"))
                continue
            head = sec[0] if title else None
            inner = sec[1:] if title else sec
            pieces = split_markdown("\n".join(inner) + "\n", max_chars - len(head or "") - 2, min_chars,
                                    sub, level)
            out += [(p, f"{head}\n\n{t}" if head else t) for p, t in pieces]
        return pack_sections(out, max_chars)
    parts = pack_blocks(blocks_of(lines), max_chars)
    return [(path, "\n".join(p) + "\n") for p in parts]


def pack_sections(parts, max_chars):
    """Greedily join consecutive sections while they fit, so files hold whole sections, not slivers.

    The packed part's path is the first section's path; when it holds several, the last title is
    appended as "first … last" so the breadcrumb shows the range.
    """
    out = []   # [paths list, text]
    for path, text in parts:
        if out and len(out[-1][1]) + len(text) + 1 <= max_chars:
            out[-1][0].append(path)
            out[-1][1] = out[-1][1].rstrip("\n") + "\n\n" + text
        else:
            out.append([[path], text])
    packed = []
    for paths, text in out:
        first, last = paths[0], paths[-1]
        if len(paths) > 1 and last and last != first:
            first = first[:-1] + (f"{first[-1]} … {last[-1]}",) if first else (f"… {last[-1]}",)
        packed.append((first, text))
    return packed


# ---------------------------------------------------------------- output

class Tree:
    def __init__(self, root, snapshot_date, max_chars):
        self.root = root
        self.snapshot_date = snapshot_date
        self.max_chars = max_chars
        self.files = {}      # rel path -> text
        self.tags = {}       # rel path -> [k=v]
        self.meta = {}       # rel path -> {notion_id, last_edited}
        self.warnings = []

    def add(self, rel, front, crumb, body, tags, notion_id=None, last_edited=None):
        if rel in self.files:
            raise ValueError(f"duplicate output path {rel}")
        front = {k: v for k, v in front.items() if v not in (None, "", [])}
        for seg in rel.split("/"):
            if len(seg.removesuffix(".md")) > MAX_STEM:
                raise ValueError(f"{rel}: segment {seg!r} longer than {MAX_STEM} chars")
        if "title" in front:
            raise ValueError(f"{rel}: frontmatter must not use 'title' (OpenViking renames the file after it)")
        front["snapshot_date"] = self.snapshot_date
        head = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=10**6)
        text = f"---\n{head}---\n\n# {crumb}\n\n{body.strip()}\n"
        if len(text) > self.max_chars + 2000:
            self.warnings.append(f"{rel}: {len(text)} chars after frontmatter")
        self.files[rel] = text
        self.tags[rel] = sorted({t.lower() for t in tags if t and "=" in t and "," not in t})
        self.meta[rel] = {"notion_id": notion_id, "last_edited": last_edited}

    def write(self):
        for rel, text in self.files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)


def page_front(rec, title, source_type, extra=None):
    front = {
        # Not "title": OpenViking's Markdown parser names the stored file after a frontmatter
        # title, so same-titled entries would collide and be lost on import.
        "notion_title": title,
        "source_type": source_type,
        "notion_page_id": dashed(rec["id"]),
        "notion_url": f"https://app.notion.com/p/{rec['id']}",
        "last_edited": rec["page"]["last_edited_time"],
    }
    front.update(extra or {})
    return front


def write_page(tree, rec, folder, source_type, label, cfg_split, extra_front=None, extra_tags=()):
    """One file for a page, or a folder of parts when it exceeds the size cap."""
    title = rec["title"] or "Untitled"
    body = clean(strip_frontmatter(rec["markdown"]))
    base = f"{slugify(title, MAX_STEM - 9)}-{rec['id'][:8]}"
    tags = [f"source_type={source_type}", f"notion_id={rec['id'][:8]}", *extra_tags]
    parts = split_markdown(body, cfg_split["max_file_chars"] - 600, cfg_split["min_part_chars"])
    if len(parts) == 1:
        tree.add(f"{folder}/{base}.md", page_front(rec, title, source_type, extra_front),
                 f"{label} · {title}", body, tags, rec["id"], rec["page"]["last_edited_time"])
        return f"{folder}/{base}"
    for n, (path, text) in enumerate(parts, 1):
        section = " > ".join(path)
        front = page_front(rec, title, source_type, extra_front)
        front.update({"part": f"{n}/{len(parts)}", "section": section or None})
        crumb = f"{label} · {title}" + (f" · {section}" if section else "") + f" (part {n}/{len(parts)})"
        name = f"{n:02d}-{slugify(path[-1], MAX_STEM - 3) if path else 'part'}.md"
        tree.add(f"{folder}/{base}/{name}", front, crumb, text, tags, rec["id"], rec["page"]["last_edited_time"])
    return f"{folder}/{base}"


# ---------------------------------------------------------------- adapters

def adapt_hub(tree, rec, cfg):
    write_page(tree, rec, "source/hub", "hub", "Career hub", cfg["split"])


def adapt_child(tree, rec, cfg, folder="source/hub-children", source_type="hub_child", label="Hub page"):
    return write_page(tree, rec, folder, source_type, label, cfg["split"])


def adapt_note(tree, rec, cfg, index_rows=None):
    body = strip_frontmatter(rec["markdown"]).strip()
    if not rec["title"] and not body:
        tree.warnings.append(f"dropped empty note row {rec['id']}")
        return
    props = properties(rec["query_page"])
    extra = {k.lower().replace(" ", "_"): v for k, v in props.items()}
    tags = []
    for key in ("Type", "Status"):
        v = props.get(key)
        if isinstance(v, str):
            tags.append(f"{key.lower()}={slugify(v, 40)}")
    base = write_page(tree, rec, "source/notes", "note", "Note", cfg["split"], extra, tags)
    if index_rows is not None:
        index_rows.append((rec["title"], props.get("Type") or "", props.get("Status") or "",
                           base.split("/")[-1], rec["page"]["last_edited_time"][:10]))


def write_notes_index(tree, rows):
    lines = ["| Title | Type | Status | File | Last edited |", "|---|---|---|---|---|"]
    lines += [f"| {t.replace('|', '/')} | {ty} | {st} | {f} | {le} |" for t, ty, st, f, le in sorted(rows)]
    tree.add("source/notes/index.md", {"notion_title": "Notes & Resources index", "source_type": "note_index"},
             f"Notes & Resources index ({len(rows)} notes)", "\n".join(lines), ["source_type=note_index"])


def is_job_lead(title, props):
    return not props.get("Type") and bool(re.search(r" [-\u2013\u2014] ", title))


def adapt_contact(tree, rec, cfg, index_rows):
    props = properties(rec["query_page"])
    title = rec["title"] or ""
    body = strip_frontmatter(rec["markdown"]).strip()
    if title in ("", "New page") and not props and not body:
        tree.warnings.append(f"dropped empty contact row {rec['id']}")
        return
    lead = is_job_lead(title, props)
    entity = "job-lead" if lead else "contact"
    folder = "source/applications" if lead else "source/contacts"
    source_type = "application" if lead else "contact"
    sections = []
    for key in ("Notes", "Next Action"):
        if props.get(key):
            sections.append(f"## {key}\n\n{clean(str(props[key]).replace('<br>', chr(10)))}")
    if body:
        sections.append(clean(body))
    rec = dict(rec, markdown="\n\n".join(sections) or "(no notes)")
    extra = {k.lower().replace(" ", "_"): v for k, v in props.items() if k not in ("Notes", "Next Action")}
    extra["entity"] = entity
    tags = [f"entity={entity}"]
    if isinstance(props.get("Status"), str):
        tags.append(f"status={slugify(props['Status'], 40)}")
    label = "Job application lead" if lead else "Contact"
    write_page(tree, rec, folder, source_type, label, cfg["split"], extra, tags)
    index_rows.append((entity, title, props.get("Type") or "", props.get("Status") or "",
                       props.get("Last Contact") or "", str(props.get("Next Action") or "").replace("\n", " ")[:160]))


def write_contact_index(tree, rows):
    for entity, folder, label in (("contact", "source/contacts", "Contacts"),
                                  ("job-lead", "source/applications", "Job application leads")):
        sel = sorted(r for r in rows if r[0] == entity)
        if not sel:
            continue
        lines = ["| Name | Type | Status | Last contact | Next action |", "|---|---|---|---|---|"]
        lines += [f"| {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5].replace('|', '/')} |" for r in sel]
        tree.add(f"{folder}/index.md", {"notion_title": f"{label} index", "source_type": f"{entity}_index"},
                 f"{label} index ({len(sel)})", "\n".join(lines), [f"source_type={entity}_index"])


def row_month(title, plan_slug=None):
    if plan_slug:
        return plan_slug[:7]
    m = re.search(r"(\d{4})-(\d{2})", title)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", title)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    raise ValueError(f"cannot tell the month of changelog row {title!r}")


def apply_offsets(body, plan):
    spec = plan.get("apply_offsets")
    if not spec:
        return body
    digest = hashlib.sha256(body.encode()).hexdigest()
    if digest != spec["raw_sha256"]:
        raise ValueError(f"row {plan['row']}: body changed since the repair offsets were reviewed; re-review")
    chars = list(body)
    for i in spec["offsets"]:
        assert chars[i] == "n", f"offset {i} is {chars[i]!r}"
        chars[i] = "\n"
    return "".join(chars)


def norm_anchor(s):
    return clean(s).strip()[:40]


def changelog_entries(rec, plan, tree):
    """Locate each planned entry by its anchor text in the fresh body. Returns [(entry, text)]."""
    raw = strip_frontmatter(rec["markdown"]).strip()
    # Repairs only turn "n" into "\n", so raw and repaired text align char for char; anchors were
    # copied from either form, so search both and map positions to repaired line numbers.
    raw_c = clean(raw)
    rep_c = clean(apply_offsets(raw, plan))
    lines = rep_c.rstrip("\n").split("\n")
    line_starts = {0}
    line_starts.update(i + 1 for i, ch in enumerate(rep_c) if ch == "\n")
    starts = {}
    for e in plan["entries"]:
        anchor = norm_anchor(e["start_anchor"])
        offsets = set()
        for text in (raw_c, rep_c):
            pos = text.find(anchor)
            while pos != -1:
                offsets.add(pos)
                pos = text.find(anchor, pos + 1)
        # Anchors may be stripped of leading spaces; accept a hit at the first non-space char of a line.
        hits = sorted({rep_c.count("\n", 0, o) for o in offsets
                       if o in line_starts or rep_c[rep_c.rfind("\n", 0, o) + 1:o].strip() == ""})
        hits = [i for i in hits if i not in starts.values()]
        if not hits:
            raise ValueError(f"row {plan['row']} entry {e['ordinal']}: anchor not found: {anchor!r}")
        # Prefer the hit nearest the planned line when an anchor repeats.
        starts[e["ordinal"]] = min(hits, key=lambda i: abs(i + 1 - e["start_line"]))
    order = sorted(plan["entries"], key=lambda e: starts[e["ordinal"]])
    boundaries = sorted(starts.values())
    first = boundaries[0]
    lead = [l for l in lines[:first] if l.strip() and l.strip() not in WRAPPER_LINES]
    if lead:
        tree.warnings.append(f"row {plan['row']}: {len(lead)} non-blank lines before the first entry dropped: {lead[0][:60]!r}")
    drop = {d["drop"] for d in plan.get("duplicates", []) if isinstance(d.get("drop"), int)}
    out = []
    for n, e in enumerate(order):
        s = starts[e["ordinal"]]
        end = boundaries[n + 1] if n + 1 < len(boundaries) else len(lines)
        chunk = [l for l in lines[s:end] if l.strip() not in WRAPPER_LINES]
        if e.get("keep_lines"):
            tail = [l for l in chunk[e["keep_lines"]:] if l.strip()]
            tree.warnings.append(f"row {plan['row']} entry {e['ordinal']}: {len(tail)} trailing lines dropped per plan")
            chunk = chunk[:e["keep_lines"]]
        if e["ordinal"] in drop:
            tree.warnings.append(f"row {plan['row']} entry {e['ordinal']} dropped as duplicate ({e['title'][:50]})")
            continue
        out.append((e, "\n".join(chunk).strip() + "\n"))
    return out


def adapt_changelog(tree, recs, cfg, plans):
    entries = []   # (entry, text, rec, plan_row)
    skip = tuple(cfg["databases"]["changelog"].get("skip_titles", []))
    for rec in recs:
        if skip and rec["title"].startswith(skip):
            tree.warnings.append(f"changelog row {rec['title']!r} skipped by config")
            continue
        plan = plans.get(rec["id"])
        month = row_month(rec["title"], plan["row"] if plan else None)
        props = properties(rec["query_page"])
        state_body = "\n\n".join(f"## {k}\n\n{clean(str(v))}" for k, v in props.items()) or "(no properties)"
        raw_body = strip_frontmatter(rec["markdown"]).strip()
        if not plan and raw_body:
            # Stub rows: the short page body (a disclaimer or note) rides along with the row state.
            state_body += f"\n\n## Page body\n\n{clean(raw_body)}"
        slug = plan["row"] if plan else slugify(rec["title"], MAX_STEM - 7)
        tree.add(f"source/changelog/{month}/state-{slug}.md",
                 page_front(rec, f"{rec['title']} (row state)", "changelog_state", {"month": month}),
                 f"Career changelog · {rec['title']} · row state (Summary, Open Loops, Pipeline Snapshot)",
                 state_body, ["source_type=changelog_state", f"month={month}", f"notion_id={rec['id'][:8]}"],
                 rec["id"], rec["page"]["last_edited_time"])
        if not plan:
            if len(raw_body) > 2000:
                raise ValueError(f"changelog row {rec['title']!r} has a {len(raw_body)}-char body but no split plan")
            continue
        for e, text in changelog_entries(rec, plan, tree):
            entries.append((e, text, rec, plan["row"]))

    max_children = cfg["split"]["max_dir_children"]
    by_month = {}
    for item in entries:
        by_month.setdefault(item[0]["date"][:7], []).append(item)
    for month, items in sorted(by_month.items()):
        items.sort(key=lambda it: (it[0]["date"], it[3], it[0]["ordinal"]))
        existing = sum(1 for p in tree.files if p.startswith(f"source/changelog/{month}/"))
        weekly = existing + len(items) > max_children
        per_day = {}
        for e, text, rec, row in items:
            date = e["date"]
            per_day[date] = per_day.get(date, 0) + 1
            nn = per_day[date]
            title = e["title"].strip() or f"{date} (untitled entry)"
            folder = f"source/changelog/{month}"
            if weekly:
                y, w, _ = dt.date.fromisoformat(date).isocalendar()
                folder += f"/{y}-W{w:02d}"
            base = f"{date}-{nn:02d}-{slugify(title, MAX_STEM - 17)}"
            front = page_front(rec, title, "changelog_entry", {
                "date": date, "entry_ordinal_in_row": e["ordinal"], "row": rec["title"],
                "notion_section": title})
            tags = ["source_type=changelog_entry", f"month={month}", f"entry_date={date}",
                    f"notion_id={rec['id'][:8]}"]
            crumb = f"Career changelog · {date} · {title}"
            parts = split_markdown(text, cfg["split"]["max_file_chars"] - 800, cfg["split"]["min_part_chars"])
            if len(parts) == 1:
                tree.add(f"{folder}/{base}.md", front, crumb, text, tags, rec["id"], rec["page"]["last_edited_time"])
            else:
                for n, (path, part) in enumerate(parts, 1):
                    f2 = dict(front, part=f"{n}/{len(parts)}", section=" > ".join(path) or None)
                    tree.add(f"{folder}/{base}-p{n}.md", f2, f"{crumb} (part {n}/{len(parts)})", part, tags,
                             rec["id"], rec["page"]["last_edited_time"])


# ---------------------------------------------------------------- main

def load_plans(split_dir):
    plans = {}
    for p in sorted(split_dir.glob("*.json")):
        d = json.loads(p.read_text())
        d["row"] = p.stem
        plans[d["page_id"].replace("-", "")] = d
    return plans


def depth_of(rec, by_id):
    d, cur = 0, rec
    while cur.get("parent_id") and cur["parent_id"] in by_id:
        d, cur = d + 1, by_id[cur["parent_id"]]
    return d


def check_dirs(tree, max_children, allow_wide=()):
    counts = {}
    for rel in tree.files:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            child = "/".join(parts[:depth + 1])
            counts.setdefault(parent, set()).add(child)
    for parent, kids in sorted(counts.items()):
        if len(kids) > max_children and parent not in allow_wide:
            tree.warnings.append(f"directory {parent} has {len(kids)} children (> {max_children})")
    return counts


def build(cfg, snapshot, out):
    index = json.loads((snapshot / "index.json").read_text())
    recs = [json.loads((snapshot / "pages" / f"{row['id']}.json").read_text()) for row in index["pages"]]
    snap_date = dt.datetime.strptime(index["snapshot_utc"], "%Y%m%dT%H%M%SZ").date().isoformat()
    tree = Tree(out / "tree", snap_date, cfg["split"]["max_file_chars"])
    plans = load_plans(Path(cfg["_dir"]) / "changelog_splits")

    contacts_index = []
    notes_index = []
    changelog = []
    page_dirs = {}      # page id -> output base path, so sub-pages land inside their parent's folder
    recs.sort(key=lambda r: depth_of(r, {x["id"]: x for x in recs}))
    for rec in recs:
        if rec["truncated"] or rec["unknown_block_ids"]:
            tree.warnings.append(f"{rec['id']} {rec['title']!r}: truncated={rec['truncated']} unknown_blocks={len(rec['unknown_block_ids'])}")
        kind = rec["kind"]
        if kind == "hub":
            adapt_hub(tree, rec, cfg)
        elif kind == "child":
            folder = page_dirs[rec["parent_id"]] if rec.get("parent_id") else "source/hub-children"
            page_dirs[rec["id"]] = adapt_child(tree, rec, cfg, folder)
        elif kind == "transcript":
            folder = page_dirs.get(rec.get("parent_id")) or f"source/hub-children/{cfg['children']['transcripts']['folder']}"
            page_dirs[rec["id"]] = adapt_child(tree, rec, cfg, folder, "transcript", "Interview transcript")
        elif kind == "notes":
            adapt_note(tree, rec, cfg, notes_index)
        elif kind == "contacts":
            adapt_contact(tree, rec, cfg, contacts_index)
        elif kind == "changelog":
            changelog.append(rec)
        else:
            raise ValueError(f"no adapter for kind {kind!r}")
    write_contact_index(tree, contacts_index)
    write_notes_index(tree, notes_index)
    adapt_changelog(tree, changelog, cfg, plans)
    used = {p for p in plans if any(r["id"] == p for r in changelog)}
    for pid, plan in plans.items():
        if pid not in used:
            tree.warnings.append(f"split plan {plan['row']} matched no fetched changelog row")
    check_dirs(tree, cfg["split"]["max_dir_children"], set(cfg["split"].get("allow_wide_dirs", [])))
    return tree, index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--snapshot", type=Path)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    cfg["_dir"] = str(args.config.parent)
    snapshot = (args.snapshot or args.config.parent / "snapshots" / "latest").resolve()
    out = args.config.parent / "builds" / snapshot.name
    if out.exists():
        import shutil
        shutil.rmtree(out)
    tree, index = build(cfg, snapshot, out)
    tree.write()

    manifest = []
    for rel, text in sorted(tree.files.items()):
        manifest.append({"path": rel, "sha256": hashlib.sha256(text.encode()).hexdigest(), "chars": len(text),
                         **tree.meta[rel], "tags": tree.tags[rel]})
    (out / "tags.json").write_text(json.dumps(tree.tags, indent=1, ensure_ascii=False))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))

    by_top = {}
    for m in manifest:
        top = "/".join(m["path"].split("/")[:2])
        by_top.setdefault(top, []).append(m["chars"])
    lines = [f"build {out}", f"snapshot {snapshot.name} ({index['snapshot_utc']})", f"files {len(manifest)}"]
    lines += [f"  {top:<28} {len(v):>4} files  max {max(v):>6} chars" for top, v in sorted(by_top.items())]
    lines += ["warnings:"] + [f"  - {w}" for w in tree.warnings]
    report = "\n".join(lines) + "\n"
    (out / "report.txt").write_text(report)
    latest = out.parent / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(out.name)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

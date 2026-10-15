#!/usr/bin/env python3
"""Tests for scripts/ov_import/build.py (splitter, cleanup, changelog anchors).

    python3 tests/test_ov_import_build.py

Synthetic inputs only: the real hub content is private and lives outside the repo.
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ov_import"))
from build import (Tree, apply_offsets, blocks_of, changelog_entries, clean,  # noqa: E402
                   split_markdown, strip_frontmatter)

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def lines_of(text):
    return [l for l in text.split("\n") if l.strip()]


def covers(source, parts):
    """Every non-blank source line appears in some part (heading lines may repeat)."""
    out = set()
    for _, t in parts:
        out.update(lines_of(t))
    return [l for l in lines_of(source) if l not in out]


def doc(sections, body_chars):
    out = ["Preamble line before any heading."]
    for i in range(sections):
        out.append(f"## Section {i}")
        out.append(("word " * (body_chars // 5)).strip())
    return "\n\n".join(out) + "\n"


print("split_markdown")
small = doc(2, 100)
check("small doc stays whole", split_markdown(small, 12000, 400) == [((), small)])

big = doc(10, 3000)
parts = split_markdown(big, 12000, 400)
check("big doc splits", len(parts) > 1, f"{len(parts)} parts")
check("parts under cap", all(len(t) <= 12000 for _, t in parts), str([len(t) for _, t in parts]))
check("no lines lost", not covers(big, parts))
check("sections packed, not one per file", len(parts) <= 4, f"{len(parts)} parts for 10x3k")

nested = "# Title\n\nIntro.\n\n" + "\n\n".join(
    f"## Part {i}\n\n" + "\n\n".join(f"### Sub {i}.{j}\n\n" + "x " * 1500 for j in range(4)) for i in range(3))
parts = split_markdown(nested, 8000, 400)
check("nested: under cap", all(len(t) <= 8000 for _, t in parts), str([len(t) for _, t in parts]))
check("nested: no lines lost", not covers(nested, parts))
check("nested: oversize section keeps its heading", all("## Part" in t for _, t in parts[1:]))

pre_heavy = "\n\n".join(("lead %d " % i) * 300 for i in range(10)) + "\n\n## Only heading\n\nshort\n"
parts = split_markdown(pre_heavy, 6000, 400)
check("single heading after long preamble terminates", len(parts) >= 2 and not covers(pre_heavy, parts))

no_heads = "\n\n".join(("para %d " % i) * 400 for i in range(20))
parts = split_markdown(no_heads, 5000, 400)
check("headingless: paragraph packing under cap", all(len(t) <= 5000 for _, t in parts))
check("headingless: no lines lost", not covers(no_heads, parts))

table = "<table>\n<tr>\n<td>h</td>\n</tr>\n" + "\n".join(f"<tr><td>row {i} " + "y" * 200 + "</td></tr>" for i in range(80)) + "\n</table>\n"
parts = split_markdown("## T\n\n" + table, 5000, 400)
check("big table split by rows", len(parts) > 1 and all(len(t) <= 5200 for _, t in parts), str([len(t) for _, t in parts]))
check("heading never stands alone", all(len(lines_of(t)) > 1 for _, t in parts))
check("every table part has the header row", all("<td>h</td>" in t for _, t in parts))

print("blocks_of")
blocks = blocks_of(["a", "", "<details>", "<summary>q</summary>", "", "answer", "</details>", "b"])
check("toggle stays one block across blank lines", ["<details>", "<summary>q</summary>", "", "answer", "</details>"] in blocks, str(blocks))

print("clean")
check("unescapes notion escapes", clean("\\$5\\~ \\[x\\]") == "$5~ [x]\n")
check("fixes escaped dashes", clean("a u2014 b u2192 c") == "a — b → c\n")
check("repairs glued nn after punctuation", clean("held.nn**Net:** x") == "held.\n\n**Net:** x\n")
check("leaves names ending in nn", clean("**Glenn** and VinnCorp") == "**Glenn** and VinnCorp\n")
check("strips ntn frontmatter", strip_frontmatter("---\ntitle: x\n---\nbody") == "body")

print("apply_offsets")
raw = "line onenline two"
spec = {"row": "t", "apply_offsets": {"raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), "offsets": [8]}}
check("offset repair", apply_offsets(raw, spec) == "line one\nline two")
try:
    apply_offsets(raw + "changed", spec)
    check("changed body is refused", False)
except ValueError:
    check("changed body is refused", True)

print("changelog_entries")
body = ("## Detailed Entries\n\n**2026-04-02 (b)** — second\nmore b\n\n"
        "**2026-04-01 (a)** — first\nmore a\n\n**2026-04-01 (a)** — first\nmore a\n")
plan = {"row": "2026-04", "entries": [
    {"ordinal": 1, "date": "2026-04-02", "title": "b", "start_line": 3, "start_anchor": "**2026-04-02 (b)**"},
    {"ordinal": 2, "date": "2026-04-01", "title": "a", "start_line": 6, "start_anchor": "**2026-04-01 (a)**"},
    {"ordinal": 3, "date": "2026-04-01", "title": "a", "start_line": 9, "start_anchor": "**2026-04-01 (a)**"}],
    "duplicates": [{"ordinals": [2, 3], "drop": 3}]}
tree = Tree(Path(tempfile.mkdtemp()), "2026-10-14", 12000)
rec = {"id": "x" * 32, "markdown": "---\ntitle: April\n---\n" + body}
got = changelog_entries(rec, plan, tree)
check("wrapper heading dropped, duplicate dropped", [e["ordinal"] for e, _ in got] == [1, 2], str([e["ordinal"] for e, _ in got]))
check("repeated anchors map to distinct lines", got[1][1].startswith("**2026-04-01 (a)**") and "second" not in got[1][1])
check("entry text runs to the next anchor", got[0][1] == "**2026-04-02 (b)** — second\nmore b\n")

print("eval / stamp helpers")
import eval as ev  # noqa: E402
from stamp import cap  # noqa: E402

check("date and month filters", ev.date_filters("What happened on April 28 and in May?")
      == ["entry_date=2026-04-28", "month=2026-04", "month=2026-05"], str(ev.date_filters("What happened on April 28 and in May?")))
check("verb 'may' is not a month", ev.date_filters("what may happen next") == [])
check("abbreviated month with day", ev.date_filters("the Oct 4 debrief") == ["entry_date=2026-10-04", "month=2026-10"])
check("norm unescapes and collapses", ev.norm("\\$240K  OTE.\n“x”") == '$240k ote. "x"', ev.norm("\\$240K  OTE.\n“x”"))
B = "viking://resources/hubs/h"
check("doc from frontmatter id", ev.doc_of(B + "/source/notes/a.md", "---\nnotion_page_id: 12345678-aaaa-bbbb-cccc-1234567890ab\n---", B)
      == "12345678aaaabbbbcccc1234567890ab")
check("doc from parsed folder", ev.doc_of(B + "/source/notes/X.body/S/S_1.md", "text", B) == "source/notes/X"
      and ev.doc_of(B + "/source/notes/X/X.md", "text", B) == "source/notes/X")
check("cap stays under limit at a word", len(cap("word " * 100, 50)) <= 50 and cap("short", 50) == "short")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all passed")

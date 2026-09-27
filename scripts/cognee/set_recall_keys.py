#!/usr/bin/env python3
"""Set the two recall keys the forked cognee plugin adds, on one profile.

    docker exec -u hermes <hermes> python3 /tmp/set_recall_keys.py \
        /opt/data/cognee.json --commit

Dry run by default. Both keys only exist in the fork pinned at `Dockerfile:897`
(`aaka3207/cognee-integrations`), so **run this only against an image built from
that pin** -- against the old plugin they are inert, `load_config` carries any
key through and only a key the code reads has an effect.

What they do, and why the obvious alternatives are wrong:

* ``search_type: "CHUNKS"`` makes recall return the stored text. Left unset, the
  server's query classifier routes a short query to an LLM completion over the
  graph, and a completion writes prose -- it paraphrases names, ids and dates,
  and where the corpus holds conversational turns it imitates them instead of
  retrieving. A per-call ``search_type`` argument still wins; the key is what
  saves a caller who forgets one. Do **not** reach for ``auto_route: false``
  instead: that pins ``GRAPH_COMPLETION``, the worst performer measured.
* ``session_writes: false`` stops the per-turn session-cache write.
  ``improve_on_end: false`` does not -- it governs only promotion into the
  permanent dataset at session end. Measured: with ``improve_on_end`` already
  off, both session tables truncated to zero refilled within five minutes.

Idempotent. Reports a no-op when the values already match, and refuses to change
a value that is already set to something else rather than silently overriding a
deliberate choice. Writes atomically (temp file + rename), preserves the existing
mode, and keeps a timestamped backup beside the file.

Never prints the file's other contents: these configs carry an api_key.
"""
import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import time

WANTED = {"search_type": "CHUNKS", "session_writes": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config_path", help="e.g. /opt/data/cognee.json")
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    ap.add_argument("--search-type", default=WANTED["search_type"],
                    help="search type to pin (default: CHUNKS)")
    args = ap.parse_args()

    wanted = dict(WANTED, search_type=args.search_type)

    if not os.path.exists(args.config_path):
        sys.exit("No such config: %s" % args.config_path)

    with open(args.config_path) as fh:
        cfg = json.load(fh)

    mode = stat.S_IMODE(os.stat(args.config_path).st_mode)
    print("%s (mode %o)" % (args.config_path, mode))

    changes, conflicts = {}, []
    for key, value in wanted.items():
        current = cfg.get(key, "(absent)")
        if key in cfg and cfg[key] == value:
            print("  %-15s already %r" % (key, value))
            continue
        if key in cfg:
            conflicts.append((key, cfg[key], value))
            continue
        print("  %-15s %r -> %r" % (key, current, value))
        changes[key] = value

    for key, current, value in conflicts:
        print("  %-15s ALREADY SET to %r, wanted %r" % (key, current, value))
    if conflicts:
        sys.exit("\nRefusing: the keys above already carry a different value. "
                 "Someone chose that; resolve by hand.")

    if not changes:
        print("\nNothing to do.")
        return

    if not args.commit:
        print("\nDRY RUN -- would set %s. Re-run with --commit."
              % ", ".join(sorted(changes)))
        return

    cfg.update(changes)

    bak = "%s.bak-recall-keys-%s" % (args.config_path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(args.config_path, bak)

    # Atomic: a half-written cognee.json takes the profile's memory offline.
    directory = os.path.dirname(os.path.abspath(args.config_path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cognee.json.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, args.config_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print("\nWritten. backup: %s" % bak)
    print("Restart gateway-default AND dashboard -- a long-running dashboard "
          "holds the old config in memory. Never restart gateway-dinefile.")


main()

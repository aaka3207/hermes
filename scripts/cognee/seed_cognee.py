#!/usr/bin/env python3
"""Load exported Mnemosyne records into a Cognee dataset.

This is the irreversible half: it spends OpenRouter tokens and writes the
target dataset (the shared, cross-agent one, in the current design). Run
scripts/cognee/export_mnemosyne.py first and read the JSONL.

Provenance (source table + timestamp) is prepended to each record's text rather
than dropped. Cognee extracts entities from the text it is given, so a bare
sentence loses when the memory was formed and which Mnemosyne bank it came
from -- which is exactly the context that makes an old memory judgeable later.
Every record is additionally prefixed with `[mnemosyne] ` so an agent reading
the shared dataset can tell this memory came from Hermes's Mnemosyne store and
not from another agent writing into the same dataset.

Each record's payload also carries `node_set: ["mnemosyne"]` on the multipart
POST, since the live API accepts a `node_set` array for machine-queryable
provenance (recall can filter via `nodeName`). Multipart array encoding for
this field is NOT verified against the live backend -- see the test file and
the task report. The `[mnemosyne] ` text prefix is the guaranteed provenance
mechanism regardless of whether node_set is honored.

Records that cannot be posted safely (bad JSON, a missing/empty `text` field)
are skipped with a warning on stderr rather than crashing the run -- a crash
mid-seed is worse than a skip, since it leaves a half-seeded store with no
record of where it stopped.

The run is resumable. A checkpoint file (default `<jsonl>.progress`, override
with --checkpoint) durably records the index of every record confirmed sent
(`<index>`) and of every record whose outcome is unknown (`?<index>`), written
append-flush-fsync after each POST. A kill mid-write can only leave a final
partial line, which is discarded on the next read because only a line
terminated by a newline is accepted. On startup the checkpoint is validated
against this run's input file, record count and dataset before anything is
skipped; a checkpoint that does not match is refused rather than silently
reused. Pass --restart to ignore an existing checkpoint and start over --
that re-POSTs every record and duplicates whatever the dataset already holds.

A retry only happens where the request provably never reached the server: a
connection refused, or a DNS failure. Every other outcome that is not a
definite success is either a definite failure (4xx, which will not improve)
or ambiguous. A lost response (timeout, connection reset) and a 5xx are BOTH
ambiguous: `/api/v1/remember` persists the payload and then runs an LLM
extraction pipeline, so a 500 can mean "written, then blew up". Retrying an
already-written record duplicates it, and nothing cleans that up. Ambiguous
records are recorded in the checkpoint so no resume re-sends them, counted
separately, listed by index at the end for manual review, and make the exit
code non-zero.

Usage:
  python3 scripts/cognee/export_mnemosyne.py mnemosyne.db > seed.jsonl
  python3 scripts/cognee/seed_cognee.py https://cognee.aakashe.org <api-key> shared seed.jsonl
  python3 scripts/cognee/seed_cognee.py ... --restart
  python3 scripts/cognee/seed_cognee.py ... --checkpoint /path/to/checkpoint
"""
import http.client
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid

# Cloudflare fronts the live deployment and answers urllib's default
# "Python-urllib/3.x" agent with its own HTTP 403 before the request reaches
# Cognee (see scripts/cognee/verify_backend.py). Send a realistic User-Agent
# or a real run fails with a 403 that looks like an auth error.
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cognee-seed/1.0 (+hermes deploy check)",
}

BOUNDARY = "----cognee-seed-%s" % uuid.uuid4().hex

# Checkpoint line prefix for a record whose outcome is unknown. A bare
# integer means "confirmed sent"; "?42" means "record 42 may or may not have
# been written -- never send it again, and tell the operator about it".
AMBIGUOUS_MARKER = "?"


class SkipRecord(Exception):
    """Raised by render() when a record cannot be sent and must be skipped."""


def _multipart(fields, filename, payload):
    """Build a multipart/form-data body.

    `fields` maps a form field name to either a scalar value or a list of
    values. A list is encoded as repeated parts sharing the same field name --
    the conventional way to send an array in multipart form data -- so
    `node_set` can carry a list of strings.
    """
    parts = []
    for name, value in fields.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            parts.append("--%s\r\n" % BOUNDARY)
            parts.append('Content-Disposition: form-data; name="%s"\r\n\r\n' % name)
            parts.append("%s\r\n" % item)
    parts.append("--%s\r\n" % BOUNDARY)
    parts.append(
        'Content-Disposition: form-data; name="data"; filename="%s"\r\n' % filename)
    parts.append("Content-Type: text/plain\r\n\r\n")
    body = "".join(parts).encode("utf-8") + payload.encode("utf-8")
    body += ("\r\n--%s--\r\n" % BOUNDARY).encode("utf-8")
    return body


def render(record):
    """Text for one memory, carrying its provenance.

    Every record is prefixed with `[mnemosyne] ` so a reader of a shared,
    multi-agent dataset can immediately tell this memory came from Hermes's
    Mnemosyne store rather than from another agent.

    Raises SkipRecord if the record has no usable text -- the caller is
    expected to skip it with a warning rather than let a KeyError kill the
    run.
    """
    if not isinstance(record, dict):
        raise SkipRecord("record is not a JSON object")
    text = record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SkipRecord("missing or empty 'text' field")
    stamp = record.get("created_at") or "unknown time"
    body = "[%s | recorded %s]\n%s" % (record.get("source", "mnemosyne"), stamp, text)
    return "[mnemosyne] " + body


def post_one(base, api_key, dataset, text, index, retries=3, backoff=1.0):
    """POST one record. Returns "sent", "failed", or "ambiguous".

    "ambiguous" means the request may already have reached the server and
    been written -- the caller must NOT retry this itself, since a retry
    could duplicate an already-written memory.

    Only failures that provably never reached the server (connection
    refused, DNS failure) and an explicit 429 are retried here.
    """
    body = _multipart({"datasetName": dataset, "node_set": ["mnemosyne"]},
                       "mnemosyne-%05d.txt" % index, text)
    url = base.rstrip("/") + "/api/v1/remember"
    attempt = 0
    while True:
        attempt += 1
        headers = dict(_HEADERS)
        headers.update({
            "X-Api-Key": api_key,
            "Content-Type": "multipart/form-data; boundary=%s" % BOUNDARY,
        })
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return "sent" if 200 <= resp.getcode() < 300 else "failed"
        except urllib.error.HTTPError as exc:
            # A definite response from the server.
            if exc.code == 429:
                # Rate-limited: the request was rejected, not written. Safe
                # to retry.
                transient_detail = "HTTP 429"
            elif 400 <= exc.code < 500:
                # Rejected outright; a retry will not improve it.
                print("  record %d: HTTP %s -- not retrying" % (index, exc.code),
                      file=sys.stderr)
                return "failed"
            else:
                # 5xx. /api/v1/remember persists the payload BEFORE running
                # the extraction pipeline, so a 500 can mean "written, then
                # blew up". Retrying would duplicate it.
                print("  record %d: HTTP %s -- NOT retrying; this record may "
                      "or may not have been written" % (index, exc.code),
                      file=sys.stderr)
                return "ambiguous"
        except (socket.timeout, TimeoutError, http.client.RemoteDisconnected,
                ConnectionResetError, BrokenPipeError) as exc:
            # The request may already have reached the server and been
            # written before the response was lost. Retrying here risks a
            # duplicate memory, so this is never retried.
            print("  record %d: response lost (%s) -- NOT retrying; this "
                  "record may or may not have been written"
                  % (index, type(exc).__name__), file=sys.stderr)
            return "ambiguous"
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
                # Failed before the request reached the server at all --
                # safe to retry.
                transient_detail = type(reason).__name__
            else:
                # Unknown failure mode (e.g. reset mid-request); treat as
                # ambiguous rather than risk a duplicate write.
                print("  record %d: response lost (%s) -- NOT retrying; this "
                      "record may or may not have been written"
                      % (index, reason), file=sys.stderr)
                return "ambiguous"
        except Exception as exc:
            print("  record %d: unexpected error (%s) -- not retrying"
                  % (index, type(exc).__name__), file=sys.stderr)
            return "failed"

        if attempt > retries:
            print("  record %d: giving up after %d attempts (%s)"
                  % (index, attempt, transient_detail), file=sys.stderr)
            return "failed"
        time.sleep(backoff * attempt)


def _read_lines(path):
    with open(path, encoding="utf-8") as handle:
        return handle.readlines()


def _checkpoint_header(path, dataset, record_count):
    return {
        "input": os.path.abspath(path),
        "dataset": dataset,
        "record_count": record_count,
    }


def _has_header(checkpoint_path):
    """True only if the checkpoint exists AND its first line is non-blank.

    A zero-length file (a `touch`, or a crash between open("w") and the
    fsync) is headerless. Treating it as an existing checkpoint would append
    bare indices under no header, and the next resume would consume index 0
    as the header -- re-sending record 0 and then crashing on `0.get(...)`.
    """
    try:
        if os.path.getsize(checkpoint_path) == 0:
            return False
    except OSError:
        return False
    with open(checkpoint_path, encoding="utf-8") as fh:
        return bool(fh.readline().strip())


def init_checkpoint(checkpoint_path, header, restart):
    """Ensure a checkpoint file with a matching header exists.

    On --restart, any existing checkpoint is discarded first. Otherwise an
    existing checkpoint is left alone (its header is validated separately by
    load_checkpoint); a missing, zero-length or headerless checkpoint gets a
    fresh header written.
    """
    exists = _has_header(checkpoint_path)
    if restart and os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
        exists = False
    if not exists:
        with open(checkpoint_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(header) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def load_checkpoint(checkpoint_path, expected_header):
    """Return (sent, ambiguous): sets of record indices not to re-send.

    `sent` were confirmed written. `ambiguous` may or may not have been
    written; either way they must never be POSTed again.

    Refuses (exits) if the checkpoint's recorded identity (input path,
    record count, dataset) does not match this run's, so a checkpoint from a
    different file or dataset can never be silently applied.

    Only a line terminated by a newline is accepted. A kill part-way through
    appending "42\\n" leaves "4" with no newline; `int("4")` would parse
    happily and silently skip record 4 while re-sending record 42.
    """
    if not os.path.exists(checkpoint_path):
        return set(), set()
    with open(checkpoint_path, encoding="utf-8") as fh:
        body = fh.read()
    if not body.strip():
        return set(), set()
    entries = body.split("\n")
    if not body.endswith("\n"):
        # Final line was never terminated: a torn write. Discard it.
        entries.pop()
    first, rest = entries[0], entries[1:]
    try:
        header = json.loads(first)
    except json.JSONDecodeError:
        header = None
    if not isinstance(header, dict):
        sys.exit("checkpoint %r is corrupt (unreadable header) -- delete "
                  "it to restart" % checkpoint_path)
    for key in ("input", "dataset", "record_count"):
        if header.get(key) != expected_header[key]:
            sys.exit(
                "checkpoint %r does not match this run (%s: %r != %r) -- "
                "it looks like it belongs to a different input file, "
                "dataset, or record count; delete the checkpoint if this "
                "mismatch is intentional, or pass --restart"
                % (checkpoint_path, key, header.get(key), expected_header[key]))
    sent = set()
    ambiguous = set()
    for line in rest:
        line = line.strip()
        if not line:
            continue
        target = sent
        if line.startswith(AMBIGUOUS_MARKER):
            target = ambiguous
            line = line[len(AMBIGUOUS_MARKER):]
        try:
            target.add(int(line))
        except ValueError:
            continue  # an unreadable line records nothing; never re-send on it
    return sent, ambiguous


def _append_checkpoint(checkpoint_path, entry):
    with open(checkpoint_path, "a", encoding="utf-8") as fh:
        fh.write("%s\n" % entry)
        fh.flush()
        os.fsync(fh.fileno())


def mark_sent(checkpoint_path, index):
    """Durably record that `index` was confirmed sent.

    Append-and-flush-and-fsync: a kill mid-write can only leave a final
    unterminated line, which load_checkpoint discards because it accepts
    only newline-terminated lines. An already-written line is never
    corrupted.
    """
    _append_checkpoint(checkpoint_path, "%d" % index)


def mark_ambiguous(checkpoint_path, index):
    """Durably record that `index`'s outcome is unknown.

    Written with the same discipline as mark_sent, under a distinct marker,
    so a resume skips it rather than re-POSTing a record that may already
    be in the dataset.
    """
    _append_checkpoint(checkpoint_path, "%s%d" % (AMBIGUOUS_MARKER, index))


def run_seed(base, api_key, dataset, path, checkpoint_path=None, restart=False,
             retries=3, backoff=1.0):
    """Seed every record in `path` into `dataset`, resumably.

    Returns a dict of counts: lines_read, total (records considered),
    resumed (already confirmed sent from a prior run), prior_ambiguous
    (recorded ambiguous by a prior run and therefore not re-sent), sent,
    skipped (malformed input), failed, ambiguous, and ambiguous_indices.
    """
    checkpoint_path = checkpoint_path or (path + ".progress")
    raw_lines = _read_lines(path)
    non_blank = [(lineno, line) for lineno, line in enumerate(raw_lines, start=1)
                 if line.strip()]
    total = len(non_blank)

    header = _checkpoint_header(path, dataset, total)
    init_checkpoint(checkpoint_path, header, restart)
    already_sent, prior_ambiguous = load_checkpoint(checkpoint_path, header)
    resumed = len(already_sent)
    if resumed:
        print("resuming: %d/%d records already confirmed sent (checkpoint %s)"
              % (resumed, total, checkpoint_path), file=sys.stderr)
    if prior_ambiguous:
        print("resuming: %d record(s) previously classified ambiguous will NOT "
              "be re-sent: %s" % (len(prior_ambiguous),
                                  sorted(prior_ambiguous)), file=sys.stderr)

    sent = 0
    skipped = 0
    failed = 0
    processed = 0
    ambiguous_indices = []

    for record_index, (lineno, raw_line) in enumerate(non_blank):
        if record_index in already_sent or record_index in prior_ambiguous:
            continue
        processed += 1
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            print("  line %d: skipping -- invalid JSON (%s)" % (lineno, exc),
                  file=sys.stderr)
            skipped += 1
            continue
        try:
            text = render(record)
        except SkipRecord as exc:
            print("  line %d: skipping -- %s" % (lineno, exc), file=sys.stderr)
            skipped += 1
            continue

        result = post_one(base, api_key, dataset, text, record_index,
                           retries=retries, backoff=backoff)
        if result == "sent":
            mark_sent(checkpoint_path, record_index)
            sent += 1
        elif result == "ambiguous":
            # Recorded durably BEFORE it is counted: if this run dies now,
            # the next resume must still know not to re-send it.
            mark_ambiguous(checkpoint_path, record_index)
            ambiguous_indices.append(record_index)
        else:
            failed += 1

        if processed % 50 == 0:
            print("  ... %d/%d processed this run (%d already done)"
                  % (processed, total - resumed - len(prior_ambiguous),
                     resumed + len(prior_ambiguous)), file=sys.stderr)

    print(
        "reconciliation: %d lines read, %d records sent, %d resumed from "
        "checkpoint, %d skipped, %d failed, %d ambiguous"
        % (len(raw_lines), sent, resumed, skipped, failed,
           len(ambiguous_indices)), file=sys.stderr)

    return {
        "lines_read": len(raw_lines),
        "total": total,
        "resumed": resumed,
        "prior_ambiguous": len(prior_ambiguous),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "ambiguous": len(ambiguous_indices),
        "ambiguous_indices": ambiguous_indices,
    }


def _parse_args(argv):
    checkpoint_path = None
    restart = False
    positional = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--restart":
            restart = True
        elif arg == "--checkpoint":
            i += 1
            if i >= len(argv):
                sys.exit("--checkpoint requires a path argument")
            checkpoint_path = argv[i]
        else:
            positional.append(arg)
        i += 1
    if len(positional) < 4:
        sys.exit("usage: seed_cognee.py <base-url> <api-key> <dataset> <jsonl> "
                  "[--restart] [--checkpoint PATH]")
    base_url, key, ds, path = positional[:4]
    return base_url, key, ds, path, checkpoint_path, restart


def main(argv):
    base_url, key, ds, path, checkpoint_path, restart = _parse_args(argv)
    if restart:
        print("WARNING: --restart discards the checkpoint and re-POSTs EVERY "
              "record in %s into dataset %r. Any record this corpus already "
              "seeded will be written a SECOND time, and a duplicate memory "
              "in a shared dataset cannot be cleaned up. Use it only on a "
              "dataset that does not already hold this corpus."
              % (path, ds), file=sys.stderr)
    print("seeding into dataset %r from %s" % (ds, path), file=sys.stderr)
    result = run_seed(base_url, key, ds, path, checkpoint_path=checkpoint_path,
                       restart=restart)
    already_done = result["resumed"] + result["prior_ambiguous"]
    print("seeded %d/%d records (%d already done, %d skipped, %d failed, "
          "%d ambiguous)"
          % (result["sent"], result["total"], already_done, result["skipped"],
             result["failed"], result["ambiguous"]))
    if result["ambiguous_indices"]:
        print("AMBIGUOUS records (0-based index into %s): %s" %
              (path, ", ".join(str(i) for i in result["ambiguous_indices"])))
        print("These may or may not have been written to dataset %r. They are "
              "recorded in the checkpoint and will NOT be re-sent by a resume, "
              "because a retry of a send that landed is a duplicate memory "
              "nothing can clean up. Check each one by hand." % ds)
    if result["skipped"] or result["failed"] or result["ambiguous"]:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])

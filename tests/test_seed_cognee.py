#!/usr/bin/env python3
"""Tests for scripts/cognee/seed_cognee.py, against a real local HTTP server.

    python3 tests/test_seed_cognee.py

A fake server is used rather than mocks so the multipart body, headers and
retry behaviour are exercised as the real backend would see them. Nothing here
touches the network or the live deployment. The dataset name used here is a
placeholder ("hermes") -- the live seed target is the `shared` dataset; see
scripts/cognee/seed_cognee.py's docstring and usage message.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from seed_cognee import render, run_seed, _checkpoint_header

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


received = []
# Controls for the stub server:
#   fail_times["n"]    -> answer the next N requests with a 503 (then 200)
#   fail_once_4xx["n"] -> answer the next N requests with a 400 (never retried)
#   hang_up["n"]       -> read the next N request bodies in full, then close
#                         the connection without answering at all
fail_times = {"n": 0}
fail_once_4xx = {"n": 0}
hang_up = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        received.append({
            "path": self.path,
            "api_key": self.headers.get("X-Api-Key"),
            "user_agent": self.headers.get("User-Agent"),
            "body": body.decode("utf-8", "replace"),
        })
        if hang_up["n"] > 0:
            # The server has the full payload -- it may well have written it --
            # and then the response is lost. This is the shape the seeder must
            # never retry.
            hang_up["n"] -= 1
            self.close_connection = True
            return
        if fail_once_4xx["n"] > 0:
            fail_once_4xx["n"] -= 1
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if fail_times["n"] > 0:
            fail_times["n"] -= 1
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')


server = HTTPServer(("127.0.0.1", 0), Handler)
base = "http://127.0.0.1:%d" % server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

workdir = tempfile.mkdtemp(prefix="seed_cognee_test_")


def jsonl_path(name):
    return os.path.join(workdir, name)


def write_jsonl(name, lines):
    path = jsonl_path(name)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")
    return path


RECORDS = [
    {"source": "memories", "text": "Prefers plain markdown.", "created_at": "2026-01-01T00:00:00"},
    {"source": "working_memory", "text": "Uses Coolify.", "created_at": "2026-09-19T13:00:00"},
]

print("\n1/12 every valid record is posted to /api/v1/remember")
del received[:]
path = write_jsonl("basic.jsonl", [json.dumps(r) for r in RECORDS])
result = run_seed(base, "test-key", "hermes", path, retries=2, backoff=0.01)
check("2 requests made", len(received) == 2, "got %d" % len(received))
check("reports 2 sent", result["sent"] == 2, "got %r" % result)
check("reports 0 skipped/failed", result["skipped"] == 0 and result["failed"] == 0,
      "got %r" % result)
check("hits /api/v1/remember",
      all(r["path"].startswith("/api/v1/remember") for r in received),
      repr([r["path"] for r in received]))

print("\n2/12 the API key is sent on every request")
check("X-Api-Key present",
      all(r["api_key"] == "test-key" for r in received),
      repr([r["api_key"] for r in received]))

print("\n3/12 the dataset name is in the payload")
check("datasetName present",
      all("hermes" in r["body"] for r in received),
      (received[0]["body"][:160] if received else "(none)"))

print("\n4/12 provenance survives into the posted text, and node_set is sent")
check("source table and timestamp included",
      any("working_memory" in r["body"] and "2026-09-19" in r["body"] for r in received),
      (received[-1]["body"][:220] if received else "(none)"))
rendered = render(RECORDS[0])
check("render() output starts with the '[mnemosyne] ' prefix",
      rendered.startswith("[mnemosyne] "), rendered[:40])
check("prefix reaches the posted body",
      all("[mnemosyne] " in r["body"] for r in received),
      (received[0]["body"][:60] if received else "(none)"))
check("node_set field carries the mnemosyne value",
      all('name="node_set"' in r["body"] and
          "mnemosyne" in r["body"].split('name="node_set"', 1)[1][:80]
          for r in received),
      (received[0]["body"][:300] if received else "(none)"))

print("\n5/12 a malformed line is skipped and counted, not crashing the run")
lines = [
    json.dumps({"source": "memories", "text": "Good record one.", "created_at": "2026-01-01"}),
    "{not valid json",
    json.dumps({"source": "memories", "created_at": "2026-01-01"}),  # missing text
    json.dumps({"source": "memories", "text": "   ", "created_at": "2026-01-01"}),  # blank text
    json.dumps({"source": "memories", "text": "Good record two.", "created_at": "2026-01-02"}),
]
del received[:]
path = write_jsonl("malformed.jsonl", lines)
result = run_seed(base, "test-key", "hermes", path, retries=1, backoff=0.01)
check("only the 2 valid records were posted", len(received) == 2, "got %d" % len(received))
check("reports 2 sent", result["sent"] == 2, "got %r" % result)
check("reports 3 skipped (bad json, missing text, blank text)",
      result["skipped"] == 3, "got %r" % result)
check("every one of the 5 lines is accounted for as sent or skipped",
      result["sent"] + result["skipped"] == result["total"] == 5,
      "got %r" % result)
check("nothing was counted as failed or ambiguous",
      result["failed"] == 0 and result["ambiguous"] == 0, "got %r" % result)

print("\n6/12 a 5xx is ambiguous, not retried: remember() persists before it "
      "can fail")
del received[:]
fail_times["n"] = 1
path = write_jsonl("retry503.jsonl", [json.dumps(RECORDS[0])])
checkpoint = path + ".progress"
if os.path.exists(checkpoint):
    os.remove(checkpoint)
result = run_seed(base, "test-key", "hermes", path, retries=2, backoff=0.01)
fail_times["n"] = 0
check("exactly 1 POST (a 503 is never retried)", len(received) == 1,
      "got %d attempts" % len(received))
check("reports 1 ambiguous, 0 sent, 0 failed",
      result["ambiguous"] == 1 and result["sent"] == 0 and result["failed"] == 0,
      "got %r" % result)
check("the ambiguous index is reported for manual review",
      result["ambiguous_indices"] == [0], "got %r" % result)
check("the ambiguous record is recorded in the checkpoint",
      os.path.exists(checkpoint) and "?0" in open(checkpoint).read(),
      open(checkpoint).read() if os.path.exists(checkpoint) else "(no file)")
del received[:]
result = run_seed(base, "test-key", "hermes", path, retries=2, backoff=0.01)
check("a resume does NOT re-send the ambiguous record", len(received) == 0,
      "got %d requests" % len(received))
check("the resume reports it as prior_ambiguous",
      result["prior_ambiguous"] == 1 and result["sent"] == 0, "got %r" % result)
os.remove(checkpoint)

print("\n7/12 a 4xx response is never retried")
del received[:]
fail_once_4xx["n"] = 1
path = write_jsonl("fail4xx.jsonl", [json.dumps(RECORDS[0])])
result = run_seed(base, "test-key", "hermes", path, retries=3, backoff=0.01)
check("exactly 1 request made (no retry on 4xx)", len(received) == 1,
      "got %d" % len(received))
check("reports 1 failed, 0 sent", result["failed"] == 1 and result["sent"] == 0,
      "got %r" % result)

print("\n8/12 a lost response is ambiguous: one POST, checkpointed, never resent")
del received[:]
hang_up["n"] = 1
path = write_jsonl("lostresponse.jsonl", [json.dumps(RECORDS[0])])
checkpoint = path + ".progress"
if os.path.exists(checkpoint):
    os.remove(checkpoint)
result = run_seed(base, "test-key", "hermes", path, retries=3, backoff=0.01)
hang_up["n"] = 0
check("exactly 1 POST (a lost response is never retried)", len(received) == 1,
      "got %d attempts" % len(received))
check("reports 1 ambiguous, 0 sent, 0 failed",
      result["ambiguous"] == 1 and result["sent"] == 0 and result["failed"] == 0,
      "got %r" % result)
check("the stub server did see the full body before the response was lost",
      received and "Prefers plain markdown" in received[0]["body"],
      received[0]["body"][:120] if received else "(none)")
check("recorded in the checkpoint under the ambiguous marker",
      os.path.exists(checkpoint) and "?0" in open(checkpoint).read(),
      open(checkpoint).read() if os.path.exists(checkpoint) else "(no file)")
del received[:]
result = run_seed(base, "test-key", "hermes", path, retries=3, backoff=0.01)
check("a resume skips it rather than duplicating the memory", len(received) == 0,
      "got %d requests" % len(received))
os.remove(checkpoint)

print("\n9/12 a zero-byte checkpoint gets a fresh header, and loses no record")
del received[:]
path = write_jsonl("zerobyte.jsonl", [json.dumps(r) for r in RECORDS])
checkpoint = path + ".progress"
open(checkpoint, "w").close()          # a `touch`, or a crash before the fsync
result = run_seed(base, "test-key", "hermes", path, retries=1, backoff=0.01)
check("both records were POSTed (record 0 was not eaten as a header)",
      len(received) == 2, "got %d" % len(received))
check("reports 2 sent, 0 resumed", result["sent"] == 2 and result["resumed"] == 0,
      "got %r" % result)
first_line = open(checkpoint, encoding="utf-8").readline()
check("a real JSON header was written over the blank file",
      first_line.strip().startswith("{") and "record_count" in first_line,
      repr(first_line))
# And the next resume reads it back cleanly rather than crashing on
# header.get() against a bare int.
del received[:]
result = run_seed(base, "test-key", "hermes", path, retries=1, backoff=0.01)
check("the rewritten checkpoint resumes cleanly",
      result["resumed"] == 2 and len(received) == 0, "got %r" % result)
os.remove(checkpoint)

print("\n10/12 a checkpoint line with no trailing newline is a torn write")
del received[:]
four = [{"source": "memories", "text": "Record %d." % i,
         "created_at": "2026-01-0%d" % (i + 1)} for i in range(4)]
path = write_jsonl("torn.jsonl", [json.dumps(r) for r in four])
checkpoint = path + ".progress"
header = _checkpoint_header(path, "hermes", 4)
with open(checkpoint, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(header) + "\n")
    fh.write("0\n")
    fh.write("3")      # torn mid-append: could just as easily have been "30"
result = run_seed(base, "test-key", "hermes", path, retries=1, backoff=0.01)
check("the torn line is discarded, not parsed as index 3",
      result["resumed"] == 1, "got %r" % result)
check("records 1, 2 and 3 were all POSTed (none silently skipped)",
      len(received) == 3, "got %d: %r" % (len(received),
                                          [r["body"][-40:] for r in received]))
check("record 3 in particular was sent",
      any("Record 3." in r["body"] for r in received),
      repr([r["body"][-40:] for r in received]))
os.remove(checkpoint)

print("\n11/12 an interrupted run resumes without re-sending already-sent records")
three = [
    {"source": "memories", "text": "Record A.", "created_at": "2026-01-01"},
    {"source": "memories", "text": "Record B.", "created_at": "2026-01-02"},
    {"source": "memories", "text": "Record C.", "created_at": "2026-01-03"},
]
path = write_jsonl("resume.jsonl", [json.dumps(r) for r in three])
checkpoint = path + ".progress"
if os.path.exists(checkpoint):
    os.remove(checkpoint)
# Simulate a process kill after records 0 and 1 were confirmed sent, by
# writing the checkpoint by hand the way mark_sent() would have, durably,
# before record 2 was ever attempted.
header = _checkpoint_header(path, "hermes", 3)
with open(checkpoint, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(header) + "\n")
    fh.write("0\n")
    fh.write("1\n")
del received[:]
result = run_seed(base, "test-key", "hermes", path, retries=1, backoff=0.01)
check("only the unsent record was POSTed", len(received) == 1,
      "got %d requests: %r" % (len(received), [r["body"][:60] for r in received]))
check("resumed reports 2 already-sent records", result["resumed"] == 2,
      "got %r" % result)
check("sent reports 1 newly-sent record", result["sent"] == 1, "got %r" % result)
check("the resumed request is for record C, not A or B",
      received and "Record C" in received[0]["body"],
      received[0]["body"][:120] if received else "(none)")
os.remove(checkpoint)

print("\n12/12 a checkpoint from a different input is refused, and --restart ignores it")
path_a = write_jsonl("mismatch_a.jsonl", [json.dumps(RECORDS[0])])
path_b = write_jsonl("mismatch_b.jsonl", [json.dumps(RECORDS[0]), json.dumps(RECORDS[1])])
checkpoint_shared = jsonl_path("mismatch.progress")
if os.path.exists(checkpoint_shared):
    os.remove(checkpoint_shared)
del received[:]
# First run creates a checkpoint tied to path_a.
run_seed(base, "test-key", "hermes", path_a, checkpoint_path=checkpoint_shared,
         retries=1, backoff=0.01)
# Reusing that checkpoint against a different input (path_b, different
# record count) must be refused, not silently applied.
refused = False
try:
    run_seed(base, "test-key", "hermes", path_b, checkpoint_path=checkpoint_shared,
             retries=1, backoff=0.01)
except SystemExit:
    refused = True
check("mismatched checkpoint raises SystemExit rather than running", refused)

del received[:]
result = run_seed(base, "test-key", "hermes", path_b, checkpoint_path=checkpoint_shared,
                   restart=True, retries=1, backoff=0.01)
check("--restart ignores the stale checkpoint and resends everything",
      len(received) == 2, "got %d" % len(received))
check("--restart reports 0 resumed", result["resumed"] == 0, "got %r" % result)
check("--restart reports 2 sent", result["sent"] == 2, "got %r" % result)

server.shutdown()
shutil.rmtree(workdir, ignore_errors=True)
print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee seed tests passed")

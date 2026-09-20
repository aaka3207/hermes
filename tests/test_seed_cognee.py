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
#   fail_times["n"]   -> answer the next N requests with a 503 (then 200)
#   fail_once_4xx["n"] -> answer the next N requests with a 400 (never retried)
fail_times = {"n": 0}
fail_once_4xx = {"n": 0}


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

print("\n1/9 every valid record is posted to /api/v1/remember")
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

print("\n2/9 the API key is sent on every request")
check("X-Api-Key present",
      all(r["api_key"] == "test-key" for r in received),
      repr([r["api_key"] for r in received]))

print("\n3/9 the dataset name is in the payload")
check("datasetName present",
      all("hermes" in r["body"] for r in received),
      (received[0]["body"][:160] if received else "(none)"))

print("\n4/9 provenance survives into the posted text, and node_set is sent")
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

print("\n5/9 a malformed line is skipped and counted, not crashing the run")
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
check("run did not crash (exception would have aborted this script)", True)

print("\n6/9 a transient 503 is retried rather than dropping a memory")
del received[:]
fail_times["n"] = 1
path = write_jsonl("retry503.jsonl", [json.dumps(RECORDS[0])])
result = run_seed(base, "test-key", "hermes", path, retries=2, backoff=0.01)
check("retried after 503", len(received) == 2, "got %d attempts" % len(received))
check("still reports 1 sent", result["sent"] == 1, "got %r" % result)

print("\n7/9 a 4xx response is never retried")
del received[:]
fail_once_4xx["n"] = 1
path = write_jsonl("fail4xx.jsonl", [json.dumps(RECORDS[0])])
result = run_seed(base, "test-key", "hermes", path, retries=3, backoff=0.01)
check("exactly 1 request made (no retry on 4xx)", len(received) == 1,
      "got %d" % len(received))
check("reports 1 failed, 0 sent", result["failed"] == 1 and result["sent"] == 0,
      "got %r" % result)

print("\n8/9 an interrupted run resumes without re-sending already-sent records")
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

print("\n9/9 a checkpoint from a different input is refused, and --restart ignores it")
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

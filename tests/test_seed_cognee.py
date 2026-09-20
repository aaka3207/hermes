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
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from seed_cognee import render, seed

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


received = []
fail_times = {"n": 0}


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

RECORDS = [
    {"source": "memories", "text": "Prefers plain markdown.", "created_at": "2026-01-01T00:00:00"},
    {"source": "working_memory", "text": "Uses Coolify.", "created_at": "2026-09-19T13:00:00"},
]

print("\n1/7 every record is posted to /api/v1/remember")
del received[:]
sent = seed(base, "test-key", "hermes", RECORDS, batch_size=1)
check("2 requests made", len(received) == 2, "got %d" % len(received))
check("reports 2 sent", sent == 2, "got %r" % sent)
check("hits /api/v1/remember",
      all(r["path"].startswith("/api/v1/remember") for r in received),
      repr([r["path"] for r in received]))

print("\n2/7 the API key is sent on every request")
check("X-Api-Key present",
      all(r["api_key"] == "test-key" for r in received),
      repr([r["api_key"] for r in received]))

print("\n3/7 the dataset name is in the payload")
check("datasetName present",
      all("hermes" in r["body"] for r in received),
      (received[0]["body"][:160] if received else "(none)"))

print("\n4/7 provenance survives into the posted text")
check("source table and timestamp included",
      any("working_memory" in r["body"] and "2026-09-19" in r["body"] for r in received),
      (received[-1]["body"][:220] if received else "(none)"))

print("\n5/7 render() prefixes every record's text with '[mnemosyne] '")
rendered = render(RECORDS[0])
check("render() output starts with the prefix", rendered.startswith("[mnemosyne] "),
      rendered[:40])
check("prefix reaches the posted body",
      all("[mnemosyne] " in r["body"] for r in received),
      (received[0]["body"][:60] if received else "(none)"))

print("\n6/7 node_set is transmitted on the multipart payload")
check("node_set field name present",
      all('name="node_set"' in r["body"] for r in received),
      (received[0]["body"] if received else "(none)"))
check("node_set carries the mnemosyne value",
      all("mnemosyne" in r["body"].split('name="node_set"', 1)[1][:80]
          for r in received if 'name="node_set"' in r["body"]),
      (received[0]["body"][:300] if received else "(none)"))

print("\n7/7 a transient 503 is retried rather than dropping a memory")
del received[:]
fail_times["n"] = 1
sent = seed(base, "test-key", "hermes", RECORDS[:1], batch_size=1, retries=2, backoff=0.01)
check("retried after 503", len(received) == 2, "got %d attempts" % len(received))
check("still reports 1 sent", sent == 1, "got %r" % sent)

server.shutdown()
print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee seed tests passed")

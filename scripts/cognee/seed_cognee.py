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

A transient failure is retried: a dropped record is a silently missing memory,
and there is no natural place to notice it.

Usage:
  python3 scripts/cognee/export_mnemosyne.py mnemosyne.db > seed.jsonl
  python3 scripts/cognee/seed_cognee.py https://cognee.aakashe.org <api-key> shared seed.jsonl
"""
import json
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
    """
    stamp = record.get("created_at") or "unknown time"
    body = "[%s | recorded %s]\n%s" % (record.get("source", "mnemosyne"), stamp,
                                       record["text"])
    return "[mnemosyne] " + body


def post_one(base, api_key, dataset, text, index, retries=3, backoff=1.0):
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
                return 200 <= resp.getcode() < 300
        except Exception as exc:
            code = getattr(exc, "code", None)
            # 4xx other than 429 will not improve on a retry.
            if code is not None and 400 <= code < 500 and code != 429:
                print("  record %d: HTTP %s -- not retrying" % (index, code),
                      file=sys.stderr)
                return False
            if attempt > retries:
                print("  record %d: giving up after %d attempts (%s)"
                      % (index, attempt, str(exc)[:120]), file=sys.stderr)
                return False
            time.sleep(backoff * attempt)


def seed(base, api_key, dataset, records, batch_size=1, retries=3, backoff=1.0):
    """Post each record. Returns the number successfully sent."""
    sent = 0
    for index, record in enumerate(records):
        if post_one(base, api_key, dataset, render(record), index,
                    retries=retries, backoff=backoff):
            sent += 1
        if (index + 1) % 50 == 0:
            print("  ... %d/%d" % (index + 1, len(records)), file=sys.stderr)
    return sent


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit("usage: seed_cognee.py <base-url> <api-key> <dataset> <jsonl>")
    base_url, key, ds, path = sys.argv[1:5]
    with open(path, encoding="utf-8") as handle:
        items = [json.loads(line) for line in handle if line.strip()]
    print("seeding %d records into dataset %r" % (len(items), ds), file=sys.stderr)
    count = seed(base_url, key, ds, items)
    print("seeded %d/%d records" % (count, len(items)))
    if count != len(items):
        sys.exit(1)

#!/usr/bin/env python3
"""A/B trial: does showing the extractor existing entity names make it reuse them?

Runs inside the cognee-backend container against two THROWAWAY datasets:

    docker cp scripts/cognee/entity_glossary_trial.py <backend>:/tmp/
    TOK=$(docker exec <cognee-mcp> printenv API_TOKEN)
    docker exec -e COGNEE_API_KEY="$TOK" <backend> python /tmp/entity_glossary_trial.py run
    docker exec <backend> python /tmp/entity_glossary_trial.py measure
    docker exec -e COGNEE_API_KEY="$TOK" <backend> python /tmp/entity_glossary_trial.py cleanup

Both arms write the same records, one /api/v1/remember call per record, in
order, with self_improvement off. The glossary arm reproduces cognee's deleted
prefetch_disambiguation POC: before each write it sends the full default
graph prompt plus every entity name already in its dataset as custom_prompt.
(custom_prompt REPLACES the system prompt, so the default must be included.)

With a trial dataset of a dozen records, "every existing name" stands in for
the POC's top-k nearest; this tests whether the model reuses names it is
shown, not how to pick them at scale.
"""
import json
import os
import sys
import threading
import time
import urllib.request
import uuid

import psycopg2

BASE = os.environ.get("COGNEE_URL", "http://localhost:8000")
PROMPT_PATH = "/app/cognee/infrastructure/llm/prompts/generate_graph_prompt.txt"
PG = dict(host=os.environ.get("DB_HOST", "cognee-postgres"),
          user=os.environ.get("DB_USERNAME", "cognee"),
          password=os.environ.get("DB_PASSWORD", ""))
ARMS = {"baseline": "entitytrial-baseline", "glossary": "entitytrial-glossary"}

RECORDS = [
    "Ameer Akashe prefers Hevy for logging workouts and wants progressive overload reviewed weekly.",
    "Ameer's deadlift personal record is 405 lb, logged in the Hevy app.",
    "The user asked that the Hevy weekly report be posted to the Hermes Discord channel.",
    "Notion is authoritative for durable state; Ameer edits pages with the ntn CLI.",
    "Prefer the Notion CLI (ntn) over the Notion web UI for bulk edits.",
    "DineFile is Ameer's restaurant-discovery app, formerly called recd, hosted on Vercel.",
    "dinefile uses Supabase and PostHog; Hermes owns its marketing content.",
    "Hermes runs on a Coolify host and keeps long-term memory in self-hosted Cognee.",
    "The cognee backend was upgraded to version 1.6.1; Hermes talks to it over HTTP.",
    "Ameer Akashe is planning an ENT procedure for a deviated septum; a cost estimate is pending.",
    "Ameer's septoplasty needs insurance pre-authorization before it can be scheduled.",
    "Claude Desktop reaches Cognee through metamcp and the cognee-mcp server.",
]

GLOSSARY_TAIL = """

# Reuse names (alias-aware)
Prefer existing entities. If a mention is an alias, abbreviation, misspelling,
shortened form or naming variant of an existing entity listed below, use that
existing name exactly instead of creating a new node. Only create a new entity
when none of these refers to the same thing.
Existing entities:
"""


def pg(dbname, sql, args=()):
    conn = psycopg2.connect(dbname=dbname, **PG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def dataset_id(name):
    rows = pg("cognee_db", "select id from datasets where name = %s", (name,))
    return str(rows[0][0]) if rows else None


def entity_names(name):
    ds = dataset_id(name)
    if not ds:
        return []
    try:
        rows = pg(ds, 'select payload from "Entity_name"')
    except psycopg2.Error:
        return []
    out = []
    for (payload,) in rows:
        p = payload if isinstance(payload, dict) else json.loads(payload)
        out.append(p.get("text") or p.get("name") or "")
    return sorted({n for n in out if n})


def remember(dataset, text, index, custom_prompt=None):
    boundary = uuid.uuid4().hex
    fields = {"datasetName": dataset, "self_improvement": "false"}
    if custom_prompt:
        fields["custom_prompt"] = custom_prompt
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="data"; '
                 f'filename="trial-{index:02d}.txt"\r\nContent-Type: text/plain\r\n\r\n')
    body = "".join(parts).encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/v1/remember", data=body, method="POST", headers={
        "X-Api-Key": os.environ["COGNEE_API_KEY"],
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.getcode()


def run_arm(arm, log):
    dataset = ARMS[arm]
    default_prompt = open(PROMPT_PATH).read()
    for i, text in enumerate(RECORDS):
        prompt = None
        if arm == "glossary":
            names = entity_names(dataset)
            if names:
                prompt = default_prompt + GLOSSARY_TAIL + "".join(f"  - {n}\n" for n in names)
        t0 = time.time()
        code = remember(dataset, text, i, prompt)
        log.append(f"{arm} #{i:02d} http={code} {time.time() - t0:.1f}s "
                   f"glossary={0 if not prompt else prompt.count(chr(10) + '  - ')}")
        print(log[-1], flush=True)


def cmd_run():
    for name in ARMS.values():
        if dataset_id(name):
            sys.exit(f"{name} already exists; run cleanup first")
    log = []
    threads = [threading.Thread(target=run_arm, args=(a, log)) for a in ARMS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def records_per_entity(name):
    """Entity -> number of distinct chunks mentioning it, read from Kuzu read-only."""
    import glob
    import kuzu
    ds = dataset_id(name)
    path = glob.glob(f"/cognee-storage/system/databases/*/{ds}.pkl")[0]
    conn = kuzu.Connection(kuzu.Database(path, read_only=True))
    res = conn.execute(
        "MATCH (c:Node)-[e:EDGE]->(n:Node) "
        "WHERE c.type = 'DocumentChunk' AND n.type = 'Entity' "
        "RETURN n.name, count(DISTINCT c.id)")
    out = {}
    while res.has_next():
        n, k = res.get_next()
        out[n] = k
    return out


def cmd_measure():
    for arm, name in ARMS.items():
        names = entity_names(name)
        print(f"\n== {arm}: {len(names)} entities")
        try:
            rpe = records_per_entity(name)
            shared = {n: k for n, k in rpe.items() if k > 1}
            print(f"   entities in >1 record: {len(shared)} of {len(rpe)}")
            for n, k in sorted(shared.items(), key=lambda x: -x[1]):
                print(f"     {k}  {n}")
        except Exception as exc:  # measurement must not hide the name list
            print(f"   (kuzu read failed: {exc})")
        print("   names: " + " | ".join(names))


def cmd_cleanup():
    for name in ARMS.values():
        ds = dataset_id(name)
        if not ds:
            print(f"{name}: absent")
            continue
        req = urllib.request.Request(f"{BASE}/api/v1/datasets/{ds}", method="DELETE",
                                     headers={"X-Api-Key": os.environ["COGNEE_API_KEY"]})
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(f"{name}: DELETE {resp.getcode()}; still present: {bool(dataset_id(name))}")


if __name__ == "__main__":
    {"run": cmd_run, "measure": cmd_measure, "cleanup": cmd_cleanup}[sys.argv[1]]()

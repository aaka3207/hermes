#!/usr/bin/env python3
"""Tests for scripts/cognee/compose_invariants.py.

Pure stdlib, no docker needed:

    python3 tests/test_compose_invariants.py

The Global Constraints in the plan are mostly ABSENCES -- no published host
ports, no floating image tags, no EMBEDDING_ENDPOINT. Absences are what review
misses, so each one gets a case that proves the checker actually catches it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "cognee"))
from compose_invariants import check_compose

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


GOOD = """
services:
  cognee-backend:
    image: cognee/cognee:1.6.0
    expose:
      - "8000"
    environment:
      - ENV=prod
      - DEBUG=false
      - ENABLE_BACKEND_ACCESS_CONTROL=true
      - FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}
      - CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org
      - DB_PROVIDER=postgres
      - VECTOR_DB_PROVIDER=pgvector
      - GRAPH_DATABASE_PROVIDER=kuzu
      - EMBEDDING_DIMENSIONS=1536
      - EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
      - LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
  cognee-mcp:
    image: cognee/cognee-mcp:main-bbec4a2
    expose:
      - "8000"
  cognee-postgres:
    image: pgvector/pgvector:pg17
"""


def case(title, text, expect_substrings):
    """expect_substrings: None (expect zero violations), a single substring,
    or a list of substrings that must each appear in some violation."""
    print("\n" + title)
    violations = check_compose(text)
    joined = " | ".join(violations)
    if expect_substrings is None:
        check("no violations", violations == [], joined)
        return
    if isinstance(expect_substrings, str):
        expect_substrings = [expect_substrings]
    for substr in expect_substrings:
        check("flags %r" % substr,
              any(substr in v for v in violations), joined or "(none)")


case("1/13 compliant compose passes", GOOD, None)
case("2/13 published host port is rejected",
     GOOD.replace('    expose:\n      - "8000"',
                  '    ports:\n      - "8000:8000"', 1),
     "ports")
case("3/13 floating :latest tag on a pinned image is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "cognee/cognee:latest"),
     "latest")
case("4/13 floating :main tag on a pinned image is rejected",
     GOOD.replace("cognee/cognee-mcp:main-bbec4a2", "cognee/cognee-mcp:main"),
     "main")
# cases 3 and 4 above don't uniquely exercise the FLOATING_TAGS branch: both
# images they mutate are also PINNED keys, so the `elif repo in PINNED`
# fallback alone would still produce a message containing "latest"/"main".
# This case uses an image that isn't in PINNED at all -- only the
# FLOATING_TAGS branch can flag it.
case("5/13 floating tag on a non-pinned image is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "myorg/sidecar:latest"),
     "latest")
case("6/13 EMBEDDING_ENDPOINT is rejected",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536",
                  "      - EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1\n"
                  "      - EMBEDDING_DIMENSIONS=1536"),
     "EMBEDDING_ENDPOINT")
case("7/13 wildcard CORS is rejected",
     GOOD.replace("CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org",
                  "CORS_ALLOWED_ORIGINS=*"),
     "CORS")
case("8/13 default JWT secret is rejected",
     GOOD.replace("FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}",
                  "FASTAPI_USERS_JWT_SECRET=super_secret"),
     "super_secret")
case("9/13 renamed service is rejected",
     GOOD.replace("  cognee-backend:", "  cognee:"),
     "cognee-backend")
case("10/13 missing EMBEDDING_DIMENSIONS is rejected as irreversible",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536\n", "", 1),
     ["EMBEDDING_DIMENSIONS", "irreversible"])
case("11/13 wrong EMBEDDING_DIMENSIONS value is rejected as irreversible",
     GOOD.replace("EMBEDDING_DIMENSIONS=1536", "EMBEDDING_DIMENSIONS=3072"),
     ["EMBEDDING_DIMENSIONS", "irreversible"])
case("12/13 missing GRAPH_DATABASE_PROVIDER is rejected",
     GOOD.replace("      - GRAPH_DATABASE_PROVIDER=kuzu\n", "", 1),
     "GRAPH_DATABASE_PROVIDER")
case("13/13 wrong GRAPH_DATABASE_PROVIDER value is rejected",
     GOOD.replace("GRAPH_DATABASE_PROVIDER=kuzu", "GRAPH_DATABASE_PROVIDER=neo4j"),
     "GRAPH_DATABASE_PROVIDER")

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all compose invariant tests passed")

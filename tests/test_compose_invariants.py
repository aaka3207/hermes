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
      - EMBEDDING_DIMENSIONS=1536
      - EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
      - LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
  cognee-mcp:
    image: cognee/cognee-mcp:main-bbec4a2
    expose:
      - "8000"
  postgres:
    image: pgvector/pgvector:pg17
"""


def case(title, text, expect_substring):
    print("\n" + title)
    violations = check_compose(text)
    joined = " | ".join(violations)
    if expect_substring is None:
        check("no violations", violations == [], joined)
    else:
        check("flags %r" % expect_substring,
              any(expect_substring in v for v in violations), joined or "(none)")


case("1/8 compliant compose passes", GOOD, None)
case("2/8 published host port is rejected",
     GOOD.replace('    expose:\n      - "8000"',
                  '    ports:\n      - "8000:8000"', 1),
     "ports")
case("3/8 floating :latest tag is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "cognee/cognee:latest"),
     "latest")
case("4/8 floating :main tag is rejected",
     GOOD.replace("cognee/cognee-mcp:main-bbec4a2", "cognee/cognee-mcp:main"),
     "main")
case("5/8 EMBEDDING_ENDPOINT is rejected",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536",
                  "      - EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1\n"
                  "      - EMBEDDING_DIMENSIONS=1536"),
     "EMBEDDING_ENDPOINT")
case("6/8 wildcard CORS is rejected",
     GOOD.replace("CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org",
                  "CORS_ALLOWED_ORIGINS=*"),
     "CORS")
case("7/8 default JWT secret is rejected",
     GOOD.replace("FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}",
                  "FASTAPI_USERS_JWT_SECRET=super_secret"),
     "super_secret")
case("8/8 renamed service is rejected",
     GOOD.replace("  cognee-backend:", "  cognee:"),
     "cognee-backend")

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all compose invariant tests passed")

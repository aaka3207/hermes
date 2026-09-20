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
      - DB_HOST=cognee-postgres
      - VECTOR_DB_PROVIDER=pgvector
      - VECTOR_DB_HOST=cognee-postgres
      - VECTOR_DB_USERNAME=cognee
      - VECTOR_DB_PASSWORD=${COGNEE_DB_PASSWORD}
      - GRAPH_DATABASE_PROVIDER=kuzu
      - EMBEDDING_DIMENSIONS=1536
      - EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
      - LLM_MODEL=openrouter/deepseek/deepseek-v4-flash
  cognee-mcp:
    image: cognee/cognee-mcp:main-bbec4a2
    expose:
      - "8000"
    environment:
      - API_URL=http://cognee-backend:8000
      - COGNEE_API_AUTH_SCHEME=x-api-key
      - MCP_ALLOWED_HOSTS=cognee-mcp:*,cognee-mcp.aakashe.org:*
  cognee-ui:
    image: cognee/cognee-ui:1.6.0
    expose:
      - "3000"

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


case("1/23 compliant compose passes", GOOD, None)
case("2/23 published host port is rejected",
     GOOD.replace('    expose:\n      - "8000"',
                  '    ports:\n      - "8000:8000"', 1),
     "ports")
case("3/23 floating :latest tag on a pinned image is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "cognee/cognee:latest"),
     "latest")
case("4/23 floating :main tag on a pinned image is rejected",
     GOOD.replace("cognee/cognee-mcp:main-bbec4a2", "cognee/cognee-mcp:main"),
     "main")
# cases 3 and 4 above don't uniquely exercise the FLOATING_TAGS branch: both
# images they mutate are also PINNED keys, so the `elif repo in PINNED`
# fallback alone would still produce a message containing "latest"/"main".
# This case uses an image that isn't in PINNED at all -- only the
# FLOATING_TAGS branch can flag it.
case("5/23 floating tag on a non-pinned image is rejected",
     GOOD.replace("cognee/cognee:1.6.0", "myorg/sidecar:latest"),
     "latest")
case("6/23 EMBEDDING_ENDPOINT is rejected",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536",
                  "      - EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1\n"
                  "      - EMBEDDING_DIMENSIONS=1536"),
     "EMBEDDING_ENDPOINT")
case("7/23 wildcard CORS is rejected",
     GOOD.replace("CORS_ALLOWED_ORIGINS=https://cognee.aakashe.org",
                  "CORS_ALLOWED_ORIGINS=*"),
     "CORS")
case("8/23 default JWT secret is rejected",
     GOOD.replace("FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}",
                  "FASTAPI_USERS_JWT_SECRET=super_secret"),
     "super_secret")
case("9/23 renamed service is rejected",
     GOOD.replace("  cognee-backend:", "  cognee:"),
     "cognee-backend")
case("10/23 missing EMBEDDING_DIMENSIONS is rejected as irreversible",
     GOOD.replace("      - EMBEDDING_DIMENSIONS=1536\n", "", 1),
     ["EMBEDDING_DIMENSIONS", "irreversible"])
case("11/23 wrong EMBEDDING_DIMENSIONS value is rejected as irreversible",
     GOOD.replace("EMBEDDING_DIMENSIONS=1536", "EMBEDDING_DIMENSIONS=3072"),
     ["EMBEDDING_DIMENSIONS", "irreversible"])
case("12/23 missing GRAPH_DATABASE_PROVIDER is rejected",
     GOOD.replace("      - GRAPH_DATABASE_PROVIDER=kuzu\n", "", 1),
     "GRAPH_DATABASE_PROVIDER")
case("13/23 wrong GRAPH_DATABASE_PROVIDER value is rejected",
     GOOD.replace("GRAPH_DATABASE_PROVIDER=kuzu", "GRAPH_DATABASE_PROVIDER=neo4j"),
     "GRAPH_DATABASE_PROVIDER")
# DB_HOST=postgres is the single mistake commit 428d21066 exists to prevent:
# it resolves to Coolify's OWN database on the shared network, and the
# failure reads as a credentials problem.
case("14/23 DB_HOST=postgres is rejected",
     GOOD.replace("DB_HOST=cognee-postgres", "DB_HOST=postgres"),
     ["DB_HOST", "cognee-postgres"])
case("15/23 missing DB_HOST is rejected",
     GOOD.replace("      - DB_HOST=cognee-postgres\n", "", 1),
     "DB_HOST")
case("16/23 VECTOR_DB_HOST=postgres is rejected",
     GOOD.replace("VECTOR_DB_HOST=cognee-postgres", "VECTOR_DB_HOST=postgres"),
     ["VECTOR_DB_HOST", "cognee-postgres"])
case("17/23 missing VECTOR_DB_PASSWORD is rejected",
     GOOD.replace("      - VECTOR_DB_PASSWORD=${COGNEE_DB_PASSWORD}\n", "", 1),
     ["VECTOR_DB_PASSWORD", "pgvector credentials"])
# Deleting the line entirely is the dangerous case: Cognee falls back to its
# own default signing secret, and the "super_secret" literal check never
# fires because there is no literal to see.
case("18/23 a deleted FASTAPI_USERS_JWT_SECRET is rejected",
     GOOD.replace("      - FASTAPI_USERS_JWT_SECRET=${COGNEE_JWT_SECRET}\n", "", 1),
     ["FASTAPI_USERS_JWT_SECRET", "default"])
case("19/23 missing COGNEE_API_AUTH_SCHEME on cognee-mcp is rejected",
     GOOD.replace("      - COGNEE_API_AUTH_SCHEME=x-api-key\n", "", 1),
     ["COGNEE_API_AUTH_SCHEME", "expires"])
case("20/23 COGNEE_API_AUTH_SCHEME=bearer is rejected",
     GOOD.replace("COGNEE_API_AUTH_SCHEME=x-api-key",
                  "COGNEE_API_AUTH_SCHEME=bearer"),
     ["COGNEE_API_AUTH_SCHEME", "x-api-key"])
case("21/23 missing MCP_ALLOWED_HOSTS is rejected",
     GOOD.replace("      - MCP_ALLOWED_HOSTS=cognee-mcp:*,"
                  "cognee-mcp.aakashe.org:*\n", "", 1),
     ["MCP_ALLOWED_HOSTS", "loopback"])
case("22/23 an MCP_ALLOWED_HOSTS entry without the ':*' suffix is rejected",
     GOOD.replace("MCP_ALLOWED_HOSTS=cognee-mcp:*,cognee-mcp.aakashe.org:*",
                  "MCP_ALLOWED_HOSTS=cognee-mcp:*,cognee-mcp.aakashe.org"),
     ["cognee-mcp.aakashe.org", "matches nothing"])

# Every case above mutates a synthetic fixture. This one binds the guard to
# the artifact that actually ships: without it, adding `ports:` to the real
# compose leaves the whole suite green.
print("\n23/23 the real deploy/cognee-selfhost.compose.yaml is compliant")
REAL = os.path.join(os.path.dirname(__file__), "..", "deploy",
                    "cognee-selfhost.compose.yaml")
with open(REAL, encoding="utf-8") as handle:
    real_text = handle.read()
real_violations = check_compose(real_text)
check("the shipped compose has zero violations", real_violations == [],
      " | ".join(real_violations))
# And the shipped compose is genuinely being read, not an empty file.
check("the shipped compose was actually loaded",
      "cognee-backend" in real_text and len(real_text) > 500,
      "%d bytes" % len(real_text))

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all compose invariant tests passed")

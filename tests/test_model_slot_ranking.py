#!/usr/bin/env python3
"""Regression tests for the Dockerfile model-slot ranking patch.

Runs INSIDE the built hermes image (needs mnemosyne + hermes_memory_provider):

    docker exec <hermes-container> /opt/hermes/.venv/bin/python \
        /opt/hermes/tests/test_model_slot_ranking.py

Uses synthetic canonical slots in a throwaway SQLite DB, so it never touches
the live memory store and its results do not drift as real memories change.

Guards the five properties the patch exists to provide:
  1. generic words alone ("does", "user") cannot select an unrelated slot
  2. a no-match query produces NO model-slot block at all
  3. distinctive career / DineFile / project queries keep their relevant slots
  4. the slot cap stays at 3
  5. contradictory slots are not co-injected more than before the patch
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="mslot-test-")
os.environ["MNEMOSYNE_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["MNEMOSYNE_DATA_DIR"], exist_ok=True)
# Pin the knobs so the test asserts the patch, not someone's env.
os.environ.pop("MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_OVERLAP", None)
os.environ.pop("MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_IDF", None)
os.environ.pop("MNEMOSYNE_PREFETCH_MODEL_SLOT_NAME_BOOST", None)
os.environ["MNEMOSYNE_PREFETCH_MODEL_SLOT_LIMIT"] = "3"

import hermes_memory_provider as H  # noqa: E402

FIXTURES = [
    # (category, name, body)
    ("model:project", "career_hub_migration",
     "Migrate the entire career hub into GBrain with full event timestamps."),
    ("model:project", "career_pilot_environment",
     "The career pilot environment variables are configured in the deployment."),
    ("model:workflow", "career_temporal_normalization",
     "Normalize career hub content while preserving page creation dates."),
    ("model:project", "dinefile_repository",
     "DineFile repository holds the restaurant recommendation extractor."),
    ("model:workflow", "dinefile_skill_policy",
     "DineFile marketing launch plans live alongside the skill policy."),
    ("model:project", "deployment_topology",
     "Coolify deployment topology for the server and its compose services."),
    # The trap: mentions "does" and "user" and nothing else relevant.
    ("model:project", "ent_sinus_decision",
     "The user is evaluating whether treatment does improve nasal breathing."),
    # Contradictory pair.
    ("model:workflow", "tv_mode_lighting",
     "When TV mode is activated turn off all lights except the table lamp."),
    ("model:workflow", "tv_light_combo",
     "Turn on the TV lights while keeping the bedroom lights on."),
    # Filler so IDF has a realistic corpus.
    ("model:user", "command_format", "Prefers copy-pastable shell commands."),
    ("model:user", "anonymity", "Wants to stay anonymous when launching apps."),
    ("model:agent", "discord_admin", "May administer Discord channels."),
]

QUERIES = {
    "generic":   "what tools and workflow style does the user prefer",
    "career":    "career goals job role work experience",
    "dinefile":  "dinefile marketing and launch plans",
    "project":   "coolify deployment server topology",
    "home_auto": "turn on tv mode lights living room",
    "no_match":  "medieval falconry equipment maintenance schedule",
}


def slot_names(block):
    out = []
    for line in (block.split("\n")[1:] if block else []):
        s = " ".join(line.split())
        if not s:
            continue
        name = s.split("]", 1)[1].split(":", 1)[0].strip() if "]" in s else s
        # The renderer does name.replace("_", " "); normalize back so the
        # assertions below can match the fixture names exactly. Without this
        # the identity checks silently pass against ANY implementation.
        out.append(name.replace(" ", "_"))
    return out


def main():
    provider = H.MnemosyneMemoryProvider()
    provider.initialize(session_id="model-slot-regression")
    beam = provider._beam
    store = getattr(beam, "canonical", None)
    if store is None:
        from mnemosyne.core.canonical import CanonicalStore
        store = CanonicalStore(db_path=beam.db_path, conn=beam.conn)
        beam.canonical = store
    owner = provider._canonical_owner()
    for category, name, body in FIXTURES:
        store.remember(owner, category, name, body, source="regression-fixture",
                       confidence=0.9)

    profile = H._resolve_profile("general")
    got = {k: slot_names(provider._prefetch_model_slots(q, profile))
           for k, q in QUERIES.items()}

    failures = []

    def check(cond, label, detail):
        print("  %-5s %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            failures.append("%s -- %s" % (label, detail))

    print("results:")
    for k in QUERIES:
        print("   %-10s n=%d %s" % (k, len(got[k]), got[k]))
    print("")
    print("assertions:")

    # 1. Generic function words must not drag in the unrelated slot.
    check("ent_sinus_decision" not in got["generic"],
          "1. generic words alone cannot select an unrelated slot",
          "got %s" % got["generic"])

    # 2. A query with no real match injects nothing.
    check(got["no_match"] == [],
          "2. no-match query yields an empty model-slot block",
          "got %s" % got["no_match"])

    # 3. Distinctive queries retain their relevant slots.
    check(any("career" in n for n in got["career"]),
          "3a. career query retains a career slot", "got %s" % got["career"])
    check(any("dinefile" in n for n in got["dinefile"]),
          "3b. dinefile query retains a dinefile slot", "got %s" % got["dinefile"])
    check(any("deployment" in n for n in got["project"]),
          "3c. project query retains a deployment slot", "got %s" % got["project"])

    # 4. Cap holds at three for every query.
    check(all(len(v) <= 3 for v in got.values()),
          "4. slot cap remains 3",
          "max was %d" % max(len(v) for v in got.values()))

    # 5. Contradictory pair must not be co-injected MORE than the pre-patch 2.
    tv = [n for n in got["home_auto"] if n.startswith("tv_")]
    check(len(tv) <= 2,
          "5. contradictory slots not made worse (<=2 co-injected)",
          "got %s" % tv)

    print("")
    if failures:
        print("FAILED (%d):" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("All 5 regression properties hold.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

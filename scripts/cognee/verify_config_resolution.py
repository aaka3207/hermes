#!/usr/bin/env python3
"""Prove what each Hermes profile resolves to with the cognee env vars removed.

Run this BEFORE deleting anything from Coolify. It strips every COGNEE_* var
from the process environment and then asks the plugin's own `load_config` what
each profile would see -- so it answers "is it safe to remove these?" without
removing them.

    docker exec -u hermes <hermes> python3 /tmp/verify_config_resolution.py

Exits non-zero if a profile would lose its service_url, dataset or api_key.
That is the whole point: a green run is the authorisation to proceed, and a red
one names the profile that still depends on the environment.

Secrets are never printed -- api_key is reported as a truncated sha256 so two
runs can be compared, and a key can be told apart from a different key, without
the value reaching a terminal or a transcript.
"""
import hashlib
import os
import sys

# Strip first, import second: config.py reads os.environ when it builds its
# defaults, so anything left here would silently pass the test.
STRIPPED = sorted(k for k in os.environ if k.startswith("COGNEE_"))
for key in STRIPPED:
    del os.environ[key]

from cognee_integration_hermes.config import load_config  # noqa: E402

# What each profile must still resolve to once the environment is empty.
EXPECTED = {
    "default": {
        "home": "/opt/data",
        "service_url": "http://cognee-backend:8000",
        "dataset": "shared",
    },
    "dinefile": {
        "home": "/opt/data/profiles/dinefile",
        "service_url": "https://tenant-acc9068f-ff2e-4085-8303-874696f1dcd7.aws.cognee.ai",
        "dataset": "dinefile",
    },
}


def sha(value):
    value = (value or "").strip()
    return hashlib.sha256(value.encode()).hexdigest()[:10] if value else "(EMPTY)"


def main():
    print("stripped from env: %s\n" % (", ".join(STRIPPED) or "(none were set)"))

    failures = []
    for name, want in EXPECTED.items():
        cfg = load_config(want["home"])
        got_url = (cfg.get("service_url") or "").strip()
        got_ds = (cfg.get("dataset") or "").strip()
        got_key = (cfg.get("api_key") or "").strip()

        print("== %s  (HERMES_HOME=%s)" % (name, want["home"]))
        print("   service_url  %s" % (got_url or "(EMPTY)"))
        print("   dataset      %s" % (got_ds or "(EMPTY)"))
        print("   api_key sha  %s" % sha(got_key))

        for field, got, expected in (
            ("service_url", got_url, want["service_url"]),
            ("dataset", got_ds, want["dataset"]),
        ):
            if got != expected:
                failures.append("%s: %s is %r, expected %r" % (name, field, got, expected))
        if not got_key:
            # Both profiles talk to a remote service, so both need a key. An
            # empty one here means that profile is still living off the env.
            failures.append("%s: api_key is empty -- still depends on COGNEE_API_KEY" % name)
        print()

    if failures:
        print("NOT SAFE TO REMOVE THE ENV VARS:")
        for f in failures:
            print("  - %s" % f)
        sys.exit(1)

    print("Both profiles resolve fully from their own cognee.json.")
    print("The COGNEE_* environment variables are dead weight and can be removed.")


main()

#!/usr/bin/env python3
"""Move a profile's Cognee API key out of the shared environment into its own
`cognee.json`, without the key ever being displayed or typed.

Background: `COGNEE_API_KEY` is set container-wide in Coolify, but its value is
*dinefile's* Cognee Cloud credential. Because the variable is container-wide,
the personal gateway also carries it -- so a script in the personal profile
that reads `os.environ` gets a working credential for dinefile's cloud tenant,
alongside a `COGNEE_BASE_URL` pointing at that same tenant. That combination is
what allowed a bulk retraction to authenticate against the wrong system on
2026-09-26 (docs/cognee-operations.md §5).

This reads the key from the environment and writes it into the target profile's
own config, so the credential ends up visible to exactly one profile.

    docker exec -u hermes <hermes> python3 /tmp/relocate_api_key.py \
        /opt/data/profiles/dinefile/cognee.json --commit

Dry run by default. Idempotent: if the file already carries the same key it
reports a no-op. Refuses to overwrite a *different* existing key, because that
would be silently swapping one profile's credential for another's.

Writes atomically (temp file + rename) and leaves the result mode 600. The
existing files are 644, which is how the personal profile's key came to be
world-readable inside the container.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile


def sha(value):
    value = (value or "").strip()
    return hashlib.sha256(value.encode()).hexdigest()[:10] if value else "(EMPTY)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config_path", help="the profile's cognee.json")
    ap.add_argument("--env-var", default="COGNEE_API_KEY")
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()

    key = os.environ.get(args.env_var, "").strip()
    if not key:
        sys.exit("%s is not set in this process -- run inside the hermes "
                 "container, where Coolify injects it." % args.env_var)

    with open(args.config_path) as fh:
        cfg = json.load(fh)

    existing = (cfg.get("api_key") or "").strip()
    print("file          %s (mode %o)"
          % (args.config_path, os.stat(args.config_path).st_mode & 0o777))
    print("env key sha   %s" % sha(key))
    print("file key sha  %s" % sha(existing))

    if existing == key:
        print("\nAlready relocated. Nothing to do.")
        # Still worth tightening the mode if a previous run predates that.
        if args.commit:
            os.chmod(args.config_path, 0o600)
            print("mode set to 600.")
        return
    if existing:
        sys.exit("\nRefusing: this file already carries a DIFFERENT api_key. "
                 "Overwriting it would swap one profile's credential for "
                 "another's. Resolve by hand.")

    cfg["api_key"] = key
    if not args.commit:
        print("\nDRY RUN -- would add api_key (sha %s) and chmod 600." % sha(key))
        print("Re-run with --commit.")
        return

    # Atomic: a half-written config.json would take the profile offline.
    directory = os.path.dirname(os.path.abspath(args.config_path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cognee.json.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, args.config_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print("\nWritten. api_key sha %s, mode 600." % sha(key))
    print("Verify with verify_config_resolution.py before touching Coolify.")


main()

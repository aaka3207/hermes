#!/usr/bin/env python3
"""Mitigate hermes-lcm #588 (SQLite POSIX-lock drop) on the runtime volume.

hermes-lcm's artifact-permission helpers (``_prepare_private_sqlite_file`` and
``_restrict_existing_sqlite_artifacts`` in ``sqlite_util.py``) do
``open(2)`` -> ``fchmod(2)`` -> ``close(2)`` on ``lcm.db`` and its ``-wal`` /
``-shm`` sidecars.

Closing ANY descriptor to a file drops that process's POSIX advisory locks on
it (https://sqlite.org/howtocorrupt.html), so this silently released SQLite's
locks -- including the ``-shm`` DMS lock that stops another process unlinking
the live WAL. A short-lived ``hermes`` CLI against the same ``HERMES_HOME``
then replaces the WAL/SHM pair while long-lived processes keep writing through
the deleted inodes, which ends in ``disk I/O error`` and then
``database disk image is malformed``.

Upstream: https://github.com/stephenschoettler/hermes-lcm/issues/588

This runs at container BOOT, not image build: hermes-lcm is not in the image,
it installs into ``$HERMES_HOME/plugins`` on the runtime volume, so no
build-time step can see it and a hand-patch there is reverted by the next
plugin update.

Exit codes:
    0  patched, already patched, plugin absent, or upstream appears fixed
    1  the lock-dropping code is still present but moved -- refuse to boot
"""
import hashlib
import os
import py_compile
import shutil
import sys
import time

MARKER = "__hermes_lcm_588_mitigation__"
TAG = "[lcm-588]"

OLD = """    if expected is not None:
        _validate_sqlite_artifact(path, expected)

    flags = os.O_RDWR"""

NEW = """    if expected is not None:
        _validate_sqlite_artifact(path, expected)
        # """ + MARKER + """ -- hermes-lcm#588, injected by the Hermes image.
        # Closing ANY descriptor to a file drops this process's POSIX advisory
        # locks on it, so the open/fchmod/close below released SQLite's locks
        # and let another process unlink the live -wal/-shm. When the artifact
        # is already private there is nothing to change: return without ever
        # opening it. Creation (expected is None) still takes the fd path,
        # which is safe because no connection exists on that path yet.
        if (
            stat.S_ISREG(expected.st_mode)
            and expected.st_nlink == 1
            and not (expected.st_mode & 0o077)
        ):
            return True

    flags = os.O_RDWR"""


def target_path(base=None):
    """Resolve the plugin file to patch for a given HERMES_HOME."""
    base = base or os.environ.get("HERMES_HOME") or "/opt/data"
    return os.path.join(base, "plugins", "hermes-lcm", "sqlite_util.py")


def apply(target):
    """Patch ``target`` in place. Returns an exit code; never raises on the
    ordinary paths so the caller can use it directly as a boot gate."""
    if not os.path.exists(target):
        print("%s hermes-lcm not installed at %s; nothing to patch" % (TAG, target))
        return 0

    src = open(target).read()
    if MARKER in src:
        print("%s already patched (marker present): %s" % (TAG, target))
        return 0

    n = src.count(OLD)
    if n != 1:
        if "os.fchmod(fd" not in src:
            print("%s anchor absent and no fchmod-on-descriptor remains; "
                  "assuming upstream fixed #588. Continuing." % TAG)
            return 0
        print("%s FATAL: anchor matched %d times but os.fchmod(fd, ...) is "
              "still present in %s. The lock-dropping code moved; refusing to "
              "boot rather than risk SQLite corruption. Re-check upstream #588."
              % (TAG, n, target), file=sys.stderr)
        return 1

    bak = "%s.bak-588-%s" % (target, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(target, bak)
    out = src.replace(OLD, NEW)

    # Atomic: write a sibling temp file, fsync, then rename over the target. A
    # plain open(target, "w") truncates first, so a crash or OOM mid-write
    # would leave a half-written plugin and a container that cannot import LCM.
    tmp = "%s.tmp-588-%d" % (target, os.getpid())
    try:
        with open(tmp, "w") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copystat(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    try:
        py_compile.compile(target, doraise=True)
    except Exception as exc:
        shutil.copy2(bak, target)
        print("%s FATAL: patched file does not compile, reverted: %s"
              % (TAG, exc), file=sys.stderr)
        return 1

    if MARKER not in open(target).read():
        shutil.copy2(bak, target)
        print("%s FATAL: marker missing after write, reverted" % TAG,
              file=sys.stderr)
        return 1

    print("%s patched %s (backup %s, sha256 %s)"
          % (TAG, target, bak, hashlib.sha256(out.encode()).hexdigest()[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(apply(target_path()))

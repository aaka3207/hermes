#!/usr/bin/env python3
"""Tests for docker/lcm-588-mitigation.py (the hermes-lcm #588 boot patcher).

Pure stdlib, no image dependencies -- runs anywhere:

    python3 tests/test_lcm_588_mitigation.py

Exercises the REAL upstream function via tests/fixtures/lcm_sqlite_util_excerpt.py
(a verbatim excerpt of hermes-lcm's sqlite_util.py), not a mock.

The load-bearing test is test_patched_module_opens_nothing, and it is only
meaningful because test_unpatched_control_opens_artifact proves the same probe
reports a *failure* against unpatched source. Without that control the check
would pass against any implementation -- the exact trap that let a regression
test in PR #29 pass against unpatched code.

Guards:
  1. control: unpatched source DOES open the artifact (the bug is real)
  2. patched source opens NO descriptor on an already-private artifact
  3. a loose (0644) file is still tightened -- the early return must not skip it
  4. idempotent: a second run is a no-op
  5. no .tmp- file is left behind (atomic write)
  6. plugin absent            -> rc 0
  7. upstream fixed           -> rc 0
  8. code moved, fchmod stays -> rc 1 (fail closed)
"""
import importlib.util as u
import os
import shutil
import stat
import sys
import tempfile

# Keep stdout in step with the patcher's unbuffered stderr, so a fail-closed
# message from a later case can't surface above the earlier PASS lines and
# make a clean run look like it started with a FATAL.
sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "lcm_sqlite_util_excerpt.py")

_spec = u.spec_from_file_location(
    "lcm588", os.path.join(ROOT, "docker", "lcm-588-mitigation.py"))
patcher = u.module_from_spec(_spec)
_spec.loader.exec_module(patcher)

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def make_home(source_path):
    """Build a throwaway HERMES_HOME containing a copy of source_path."""
    home = tempfile.mkdtemp(prefix="lcm588-home-")
    d = os.path.join(home, "plugins", "hermes-lcm")
    os.makedirs(d)
    shutil.copy2(source_path, os.path.join(d, "sqlite_util.py"))
    return home


def load(module_path, name):
    spec = u.spec_from_file_location(name, module_path)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe_opens(module_path, name, mode=0o600):
    """Call the real _chmod_sqlite_artifact_at and record which artifacts it
    opened. Returns (return_value, [opened names], resulting mode)."""
    m = load(module_path, name)
    d = tempfile.mkdtemp(prefix="lcm588-probe-")
    os.chmod(d, 0o700)
    from pathlib import Path
    p = Path(d) / "lcm.db"
    p.write_bytes(b"x")
    os.chmod(p, mode)

    real_open = os.open
    opened = []

    def counting_open(path, flags, fmode=0o777, *, dir_fd=None):
        if dir_fd is not None:
            opened.append(path)
            return real_open(path, flags, fmode, dir_fd=dir_fd)
        return real_open(path, flags, fmode)

    os.open = counting_open
    try:
        dfd = m._open_private_sqlite_directory(p)
        try:
            rv = m._chmod_sqlite_artifact_at(p, directory_fd=dfd, create=False)
        finally:
            os.close(dfd)
    finally:
        os.open = real_open
    return rv, opened, stat.S_IMODE(os.stat(p).st_mode)


print("\n1/8 control: unpatched source must open the artifact")
rv, opened, _ = probe_opens(FIXTURE, "ctl_unpatched")
check("unpatched control opens the artifact (bug reproduced)",
      opened == ["lcm.db"], "opened=%s" % opened)

print("\n2/8 patched source must open nothing")
home = make_home(FIXTURE)
target = patcher.target_path(home)
rc = patcher.apply(target)
check("apply() returns 0", rc == 0)
check("marker present after patch", patcher.MARKER in open(target).read())
rv, opened, mode = probe_opens(target, "patched_private")
check("patched helper returns True", rv is True)
check("patched helper opens NO descriptor", opened == [], "opened=%s" % opened)
check("mode still 0600", mode == 0o600, oct(mode))

print("\n3/8 a loose 0644 file must still be tightened")
rv, opened, mode = probe_opens(target, "patched_loose", mode=0o644)
check("loose file still opened (early return correctly skipped)",
      opened == ["lcm.db"], "opened=%s" % opened)
check("loose file tightened to 0600", mode == 0o600, oct(mode))

print("\n4/8 idempotency + 5/8 atomic write leaves no temp file")
rc2 = patcher.apply(target)
check("second apply() returns 0", rc2 == 0)
leftovers = [f for f in os.listdir(os.path.dirname(target)) if ".tmp-588-" in f]
check("no .tmp-588- file left behind", leftovers == [], str(leftovers))
backups = [f for f in os.listdir(os.path.dirname(target)) if ".bak-588-" in f]
check("exactly one backup written", len(backups) == 1, str(backups))
shutil.rmtree(home, ignore_errors=True)

print("\n6/8 plugin absent -> rc 0")
check("absent plugin exits 0",
      patcher.apply("/nonexistent-lcm/plugins/hermes-lcm/sqlite_util.py") == 0)

print("\n7/8 upstream fixed (anchor gone, no fchmod-on-fd) -> rc 0")
src = open(FIXTURE).read()
fixed = src.replace("        os.fchmod(fd, 0o600)", "        pass  # upstream fixed")
fixed = fixed.replace("    flags = os.O_RDWR", "    flags = 0  # restructured")
home = make_home(FIXTURE)
t = patcher.target_path(home)
open(t, "w").write(fixed)
check("upstream-fixed source exits 0", patcher.apply(t) == 0)
shutil.rmtree(home, ignore_errors=True)

print("\n8/8 code moved but fchmod remains -> rc 1 (fail closed)")
moved = src.replace("    flags = os.O_RDWR", "    flags = 0  # moved")
home = make_home(FIXTURE)
t = patcher.target_path(home)
open(t, "w").write(moved)
check("moved-code source exits 1", patcher.apply(t) == 1)
check("moved-code source left unmodified",
      patcher.MARKER not in open(t).read())
shutil.rmtree(home, ignore_errors=True)

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all lcm-588 mitigation tests passed")

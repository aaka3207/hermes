#!/usr/bin/env python3
"""Tests for docker/cognee-remember-filename.py (the cognee #436 patcher).

Pure stdlib, no image dependencies -- runs anywhere:

    python3 tests/test_cognee_remember_filename.py

The load-bearing test is test_patched_names_differ, and it is only meaningful
because test_unpatched_control_collides proves the same probe reports a
*collision* against unpatched source. Without that control, a check that
"two writes produce two names" would pass against any implementation that
happened to return something -- including one still sending memory.txt.

Guards:
  1. control: unpatched source sends ONE name for two different texts
  2. patched source sends distinct names for distinct content
  3. patched source sends the SAME name for byte-identical content, so the
     re-add stays the no-op cognee documents
  4. the fixed "memory.txt" is gone entirely
  5. idempotent -- a second apply is a no-op, not a double patch
  6. anchor moved while memory.txt remains -> exit 1 (refuse to boot)
  7. upstream/fork fixed it properly (content-derived name) -> exit 0
  8. upstream swapped one fixed name for another -> exit 1. "memory.txt is
     gone" is not the same as "the bug is fixed": any other fixed name 409s
     identically, so the absence of the old literal is not enough to pass.
  9. plugin absent -> exit 0
 10. target_path survives a python3.x minor bump
"""
import importlib.util as u
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docker"))
spec = u.spec_from_file_location(
    "cognee_remember_filename",
    os.path.join(os.path.dirname(__file__), "..", "docker",
                 "cognee-remember-filename.py"))
mod = u.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


# A stand-in for http_backend.py carrying the REAL anchor line verbatim, at
# the real indentation (inside a method). `_multipart_body` returns the files
# dict so a probe can read back the filename that would have been sent.
FIXTURE = '''
def _multipart_body(fields, files):
    return files


class HttpBackend:
    def _remember(self, *, text, dataset, session_id, timeout):
        fields = {"datasetName": dataset}
''' + mod.OLD + '''
        return multipart
'''


def write_fixture(body=FIXTURE):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w") as fh:
        fh.write(body)
    return path


def sent_name(path, text):
    """Load the module at *path* and return the filename it would upload."""
    name = "fx_%d" % abs(hash(path + text))
    s = u.spec_from_file_location(name, path)
    m = u.module_from_spec(s)
    s.loader.exec_module(m)
    files = m.HttpBackend()._remember(
        text=text, dataset="d", session_id="", timeout=1)
    return files["data"][0]


print("\n1/10 control: unpatched source collides on one fixed name")
p = write_fixture()
a, b = sent_name(p, "alpha one"), sent_name(p, "beta two, different")
check("unpatched sends memory.txt for both", a == b == "memory.txt",
      "%r vs %r" % (a, b))

print("\n2/10 patched source sends distinct names for distinct content")
rc = mod.apply(p)
check("apply() returned 0", rc == 0, "rc=%d" % rc)
pa, pb = sent_name(p, "alpha one"), sent_name(p, "beta two, different")
check("names differ", pa != pb, "%r vs %r" % (pa, pb))
check("both are memory-<hash>.txt",
      pa.startswith("memory-") and pa.endswith(".txt")
      and pb.startswith("memory-") and pb.endswith(".txt"),
      "%r, %r" % (pa, pb))

print("\n3/10 byte-identical content keeps one name (the documented no-op)")
check("same text -> same name", sent_name(p, "alpha one") == pa, pa)

print("\n4/10 the fixed name is gone")
check("no memory.txt left in source", '"memory.txt"' not in open(p).read())

print("\n5/10 idempotent")
rc2 = mod.apply(p)
check("second apply is a no-op 0", rc2 == 0, "rc=%d" % rc2)
check("marker appears exactly once",
      open(p).read().count(mod.MARKER) == 1,
      str(open(p).read().count(mod.MARKER)))
os.unlink(p)

print("\n6/10 anchor moved but memory.txt remains -> refuse")
moved = write_fixture(FIXTURE.replace(
    mod.OLD,
    '        multipart = _multipart_body(fields, {"data": ("memory.txt", '
    'text.encode("utf-8"), "text/plain")})'))
rc3 = mod.apply(moved)
check("returns 1", rc3 == 1, "rc=%d" % rc3)
os.unlink(moved)

print("\n7/10 fixed upstream with a content-derived name -> do not block")
fixed = write_fixture(FIXTURE.replace(
    mod.OLD,
    '        blob = text.encode("utf-8")\n'
    '        _n = "memory-%s.txt" % hashlib.sha256(blob).hexdigest()[:16]\n'
    '        multipart = _multipart_body(fields, {"data": (_n, blob)})'))
rc4 = mod.apply(fixed)
check("returns 0", rc4 == 0, "rc=%d" % rc4)
os.unlink(fixed)

print("\n8/10 one fixed name swapped for another -> refuse")
# The whole point of the positive check. memory.txt is gone, so the old
# "assume upstream fixed it" branch passed this and shipped the 409 outage.
renamed = write_fixture(FIXTURE.replace(
    mod.OLD,
    '        multipart = _multipart_body(fields, '
    '{"data": ("memory.md", text.encode("utf-8"))})'))
rc5 = mod.apply(renamed)
check("returns 1", rc5 == 1, "rc=%d" % rc5)
os.unlink(renamed)

print("\n9/10 plugin absent -> 0")
check("returns 0", mod.apply("/nonexistent/http_backend.py") == 0)

print("\n10/10 target_path survives a python3.x minor bump")
tmp = tempfile.mkdtemp()
deep = os.path.join(tmp, "lib", "python3.99", "site-packages",
                    "cognee_integration_hermes")
os.makedirs(deep)
open(os.path.join(deep, "http_backend.py"), "w").write("x = 1\n")
resolved = mod.target_path(tmp)
check("globs python3.99", resolved.endswith(
    "python3.99/site-packages/cognee_integration_hermes/http_backend.py"),
    resolved)

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee remember-filename tests passed")

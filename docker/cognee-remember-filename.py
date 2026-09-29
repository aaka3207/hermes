#!/usr/bin/env python3
"""Give each Cognee `remember` a content-derived upload filename -- or prove it
already has one.

**Since the Dockerfile pin moved to the fork, this normally patches nothing.**
The fork carries the fix in its own source, so the anchor below is absent and
the script runs its verification path instead: it asserts the installed
``_remember`` derives its upload name from the content, and fails the build if
it does not. That is what makes a bad fork rebase loud rather than silent. The
patching path is kept because it still applies to the upstream pin, so a
rollback needs no change here.

`cognee-integration-hermes-agent`'s ``HttpBackend._remember`` uploads every
permanent memory as a multipart file literally named ``memory.txt``. The
package pins ``cognee==1.5.4``, where ``add()`` silently replaced a
same-named document, so the fixed name was harmless there.

Against a **cognee >= 1.6.0 server it is fatal**. 1.6.0 stopped letting
``add()`` replace a same-named document whose content differs and raises
``DocumentUpdateRequiredError`` (HTTP 409) instead. So the first `remember`
in a dataset creates ``memory.txt`` and every later one with different text
bounces off it, forever.

The failure hides because it is asymmetric: **recall keeps working, and the
Claude Desktop path keeps working**, because cognee content-addresses raw
text as ``text_<md5>.txt`` and the MCP route sends text rather than a named
file. Memory looks healthy and stores nothing. Measured in this estate: it
ran undetected from 2026-09-21 15:40 to 2026-09-23, and the tell was the
naming -- 1,404 documents in `shared`, all ``text_<md5>`` except one row
literally named ``memory``.

The fix restores the property cognee already relies on for text: derive the
name from the content. Distinct memories become distinct documents, and
byte-identical content re-adds as the documented no-op.

Two things deliberately NOT done:

* **Not ``cognee.update(data_id=...)``**, which is what the 409's own message
  advises. Each `remember` is a new fact, not a revision of one document.
  Wiring ``_remember`` to ``update`` would make every memory overwrite the
  previous one -- data loss that presents as success.
* **Not named ``text_<md5>.txt``.** cognee's ``save_data_to_file.py`` notes
  that other code constructs and asserts on that exact form, so a same-shaped
  name carrying a differently-computed hash risks tripping those assertions.

``hashlib`` is imported inside the method rather than at module scope so the
whole change is one contiguous replacement. A second, distant edit to the
import block would be another way for this patch to half-apply; the import is
cached after first use, so the cost is nil.

Upstream: topoteretes/cognee-integrations#436. Remove this file once a PyPI
release carrying the fix is pinned in the Dockerfile.

Exit codes:
    0  patched, already patched, plugin absent, or the installed source was
       verified to derive the upload name from the content
    1  a fixed upload filename is still in place -- the old ``memory.txt`` with
       a moved anchor, or any other fixed name, which 409s identically. Refuse
       to boot rather than run with writes that fail silently.
"""
import glob
import hashlib
import os
import py_compile
import shutil
import sys
import time

MARKER = "__hermes_cognee_remember_filename__"
TAG = "[cognee-remember-filename]"

OLD = ('        multipart = _multipart_body(fields, '
       '{"data": ("memory.txt", text.encode("utf-8"))})')

NEW = ('        # ' + MARKER + ''' -- cognee-integrations#436, injected by the
        # Hermes image. A fixed upload name makes cognee >= 1.6.0 reject every
        # write after the first with DocumentUpdateRequiredError (409), because
        # add() will not replace a same-named document whose content differs.
        # Deriving the name from the content gives each memory its own
        # document and makes an identical re-add the documented no-op.
        import hashlib

        _payload = text.encode("utf-8")
        _name = "memory-%s.txt" % hashlib.sha256(_payload).hexdigest()[:16]
        multipart = _multipart_body(fields, {"data": (_name, _payload)})''')


def target_path(base=None):
    """Resolve the plugin file to patch inside a virtualenv.

    Globs the python3.* directory rather than hardcoding a version, so a base
    image that moves from 3.13 to 3.14 does not silently turn this into a
    no-op ("plugin absent") and reintroduce the bug.
    """
    base = base or os.environ.get("HERMES_VENV") or "/opt/hermes/.venv"
    pattern = os.path.join(
        base, "lib", "python3.*", "site-packages",
        "cognee_integration_hermes", "http_backend.py")
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else pattern.replace("python3.*", "python3")


def verify_fixed_upstream(target, src):
    """Assert the installed source already derives the upload name itself.

    Reached when the patch anchor is gone and no ``"memory.txt"`` remains, which
    is the shape of a tree that carries the fix -- the fork pinned in the
    Dockerfile, or eventually an upstream release. "The old literal is absent"
    is too weak to accept on its own: renaming it to any other fixed value, say
    ``memory.md``, also removes the literal and reintroduces the 409 outage in
    full. So check for the fix positively instead.

    Both markers must appear in the same ``_remember`` body. Scanning the whole
    module would pass on a ``hashlib`` import used somewhere else entirely.
    """
    body = remember_body(src)
    if body is None:
        print("%s FATAL: no _remember found in %s. The method was renamed or "
              "removed; refusing to boot rather than assume #436 is fixed."
              % (TAG, target), file=sys.stderr)
        return 1

    if "hashlib" not in body or "sha256" not in body:
        print("%s FATAL: _remember in %s names no content-derived upload file "
              "(no hashlib/sha256 in its body). The fixed name may have been "
              "replaced by another fixed name, which 409s exactly the same way. "
              "Re-check upstream #436." % (TAG, target), file=sys.stderr)
        return 1

    print("%s upstream fix verified: _remember derives its upload name from the "
          "content. %s" % (TAG, target))
    return 0


def remember_body(src):
    """The source of ``_remember``, or None. Ends at the next def at its indent."""
    start = src.find("    def _remember(")
    if start == -1:
        return None
    rest = src[start + 1:]
    end = rest.find("\n    def ")
    return rest if end == -1 else rest[:end]


def apply(target):
    """Patch ``target`` in place. Returns an exit code; never raises on the
    ordinary paths so the caller can use it directly as a build/boot gate."""
    if not os.path.exists(target):
        print("%s cognee plugin not installed at %s; nothing to patch"
              % (TAG, target))
        return 0

    src = open(target).read()
    if MARKER in src:
        print("%s already patched (marker present): %s" % (TAG, target))
        return 0

    n = src.count(OLD)
    if n != 1:
        if '"memory.txt"' not in src:
            return verify_fixed_upstream(target, src)
        print('%s FATAL: anchor matched %d times but "memory.txt" is still '
              "present in %s. The upload-name code moved; refusing to boot "
              "rather than run with memory writes that 409 silently. "
              "Re-check upstream #436." % (TAG, n, target), file=sys.stderr)
        return 1

    bak = "%s.bak-436-%s" % (target, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(target, bak)
    out = src.replace(OLD, NEW)

    # Atomic: write a sibling temp file, fsync, then rename over the target. A
    # plain open(target, "w") truncates first, so a crash mid-write would
    # leave a half-written module and a container that cannot import the
    # memory provider at all.
    tmp = "%s.tmp-436-%d" % (target, os.getpid())
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

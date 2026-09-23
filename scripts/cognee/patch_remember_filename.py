#!/usr/bin/env python3
"""Give each Hermes `remember` a content-derived upload filename.

cognee-integration-hermes-agent 1.2.2 pins cognee==1.5.4 and uploads every
permanent memory as a file literally named `memory.txt`. Against cognee
>= 1.6.0 that is fatal: add() no longer replaces a same-named document whose
content differs, it raises DocumentUpdateRequiredError (HTTP 409). So the
first remember in a dataset wins and every later one bounces off it, while
reads and the MCP path stay healthy -- memory looks fine but stores nothing.

cognee content-addresses raw text as `text_<md5>.txt` for exactly this
reason. We deliberately do NOT reuse that spelling: cognee constructs and
asserts on it elsewhere, so a same-shaped name with a differently-computed
hash could trip an assertion. `memory-<sha256[:16]>.txt` is collision-free
and unmistakably ours.

Idempotent: running it twice is a no-op. Reverted by any Hermes redeploy,
which reinstalls site-packages from the image.
"""
import re
import shutil
import sys

PATH = ("/opt/hermes/.venv/lib/python3.13/site-packages/"
        "cognee_integration_hermes/http_backend.py")
OLD = ('        multipart = _multipart_body(fields, '
       '{"data": ("memory.txt", text.encode("utf-8"))})')
NEW = ('        _payload = text.encode("utf-8")\n'
       '        _name = "memory-%s.txt" % '
       'hashlib.sha256(_payload).hexdigest()[:16]\n'
       '        multipart = _multipart_body(fields, {"data": '
       '(_name, _payload)})')

src = open(PATH, encoding="utf-8").read()

if "_name = \"memory-%s.txt\"" in src:
    print("already patched; nothing to do")
    sys.exit(0)

if OLD not in src:
    sys.exit("FAILED: the target line is not present verbatim -- the package "
             "version changed. Re-read http_backend.py before patching.")

shutil.copy2(PATH, PATH + ".bak-memoryname")

src = src.replace(OLD, NEW, 1)
if not re.search(r"^import hashlib$", src, re.M):
    src = src.replace("import json\n", "import hashlib\nimport json\n", 1)

open(PATH, "w", encoding="utf-8").write(src)
print("patched %s (backup at %s.bak-memoryname)" % (PATH, PATH))

#!/usr/bin/env python3
"""Tests for docker/cognee-cloud-smoke.py (the --no-deps Cognee Cloud guard).

Pure stdlib, no image dependencies -- runs anywhere:

    python3 tests/test_cognee_cloud_smoke.py

The smoke check exists to defend one decision: the image installs
cognee-integration-hermes-agent WITHOUT its cognee==1.5.x pin, which is only
safe while the cloud path never imports that package. These tests prove the
check actually detects each way that could stop being true, against a fake
package built here -- so they do not need network or a real install.

The load-bearing test is test_import_mask_is_real. Masking `cognee` and then
observing "import cognee fails" proves nothing when cognee is not installed --
it fails either way. So the check masks a module that provably DOES exist and
fails if the mask lets it through; the test below proves that control fires by
running the same script with the legacy find_module API, which Python 3.12
removed and therefore never consults.

Guards:
  1. clean cloud-mode package            -> rc 0
  2. control: legacy finder API          -> rc 1 (the mask is not real)
  3. module-scope cognee import          -> rc 1
  4. missing entry point (pre-1.2.1)     -> rc 1
  5. embedded mode on by default         -> rc 1
  6. cloud config selecting SdkBackend   -> rc 1
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMOKE = os.path.join(ROOT, "docker", "cognee-cloud-smoke.py")

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def build_pkg(tmp, *, entry_point=True, module_scope_cognee=False,
              embedded_default=False, backend="HttpBackend"):
    """Write a fake cognee_integration_hermes + dist-info into a fresh dir."""
    site = os.path.join(tmp, "site")
    pkg = os.path.join(site, "cognee_integration_hermes")
    os.makedirs(pkg)

    head = "import cognee\n" if module_scope_cognee else ""
    with open(os.path.join(pkg, "__init__.py"), "w") as fh:
        fh.write(head + "from .provider import CogneeMemoryProvider\n")
    with open(os.path.join(pkg, "provider.py"), "w") as fh:
        fh.write("class CogneeMemoryProvider:\n    pass\n")
    with open(os.path.join(pkg, "config.py"), "w") as fh:
        fh.write(textwrap.dedent("""
            import os
            def load_config(home=None):
                return {
                    "service_url": os.environ.get("COGNEE_BASE_URL", ""),
                    "embedded": %s,
                    "transport": os.environ.get("COGNEE_TRANSPORT", ""),
                }
        """ % ("True" if embedded_default else
               'os.environ.get("COGNEE_EMBEDDED", "") == "true"')))
    with open(os.path.join(pkg, "backend.py"), "w") as fh:
        fh.write(textwrap.dedent("""
            class HttpBackend: pass
            class SdkBackend: pass
            def build_backend(config=None):
                return %s()
        """ % backend))

    dist = os.path.join(site, "cognee_integration_hermes-1.2.1.dist-info")
    os.makedirs(dist)
    with open(os.path.join(dist, "METADATA"), "w") as fh:
        fh.write("Metadata-Version: 2.1\nName: cognee-integration-hermes-agent\n"
                 "Version: 1.2.1\n")
    with open(os.path.join(dist, "INSTALLER"), "w") as fh:
        fh.write("pip\n")
    if entry_point:
        with open(os.path.join(dist, "entry_points.txt"), "w") as fh:
            fh.write("[hermes_agent.memory_providers]\n"
                     "cognee = cognee_integration_hermes\n")
    return site


def run(site, script=SMOKE):
    env = dict(os.environ)
    env["PYTHONPATH"] = site
    env.pop("COGNEE_BASE_URL", None)
    env.pop("COGNEE_EMBEDDED", None)
    env.pop("COGNEE_TRANSPORT", None)
    p = subprocess.run([sys.executable, script], env=env,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


print("\n1/6 a clean cloud-mode package passes")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
rc, out = run(build_pkg(tmp))
check("clean package exits 0", rc == 0, out.strip()[:160])
check("reports the three guarantees", "HttpBackend" in out, out.strip()[:160])
shutil.rmtree(tmp, ignore_errors=True)

print("\n2/6 control: a mask using the removed legacy API must be caught")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
legacy = os.path.join(tmp, "legacy-smoke.py")
src = open(SMOKE).read()
old = "        def find_spec(self, name, path=None, target=None):"
assert old in src, "smoke script no longer uses find_spec; update this control"
open(legacy, "w").write(src.replace(old, "        def find_module(self, name, path=None):"))
rc, out = run(build_pkg(tmp), script=legacy)
check("legacy-API mask exits 1", rc == 1, out.strip()[:160])
check("names the mask as the failure", "mask is not in effect" in out,
      out.strip()[:160])
shutil.rmtree(tmp, ignore_errors=True)

print("\n3/6 a module-scope cognee import must fail the build")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
rc, out = run(build_pkg(tmp, module_scope_cognee=True))
check("module-scope cognee exits non-zero", rc != 0, out.strip()[:160])
check("surfaces the masked import", "deliberately masked" in out,
      out.strip()[:200])
shutil.rmtree(tmp, ignore_errors=True)

print("\n4/6 a missing entry point (1.0.0-1.2.0 shape) must fail the build")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
rc, out = run(build_pkg(tmp, entry_point=False))
check("no entry point exits 1", rc == 1, out.strip()[:160])
check("cites issue #382", "#382" in out, out.strip()[:200])
shutil.rmtree(tmp, ignore_errors=True)

print("\n5/6 embedded-by-default must fail the build")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
rc, out = run(build_pkg(tmp, embedded_default=True))
check("embedded default exits 1", rc == 1, out.strip()[:160])
check("says --no-deps is unsafe", "--no-deps is unsafe" in out, out.strip()[:200])
shutil.rmtree(tmp, ignore_errors=True)

print("\n6/6 a cloud config that selects SdkBackend must fail the build")
tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
rc, out = run(build_pkg(tmp, backend="SdkBackend"))
check("SdkBackend exits 1", rc == 1, out.strip()[:160])
check("names the chosen backend", "SdkBackend" in out, out.strip()[:200])
shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee-cloud smoke tests passed")

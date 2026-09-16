#!/usr/bin/env python3
"""Tests for docker/cognee-cloud-smoke.py (the Cognee Cloud build guard).

Pure stdlib, no image dependencies -- runs anywhere:

    python3 tests/test_cognee_cloud_smoke.py

Builds a fake `cognee_integration_hermes` (plus, where wanted, a fake `cognee`)
in a temp dir and runs the real smoke script against it, so no network or real
install is needed.

The load-bearing pair is 1 and 2. They differ ONLY in whether a `cognee`
package is on the path, and the fake provider's is_available() uses the same
logic as upstream (`find_spec("cognee")` first, then the service URL). So 2 is
the exact regression #31 shipped -- plugin installed, importable, configured for
cloud, and still unavailable -- and 1 proves the check does not simply fail on
everything.

Guards:
  1. cognee importable + cloud config        -> rc 0
  2. cognee NOT importable (the #31 shape)   -> rc 1
  3. missing entry point (pre-1.2.1)         -> rc 1
  4. is_available() False despite cognee     -> rc 1
  5. cloud config selecting SdkBackend       -> rc 1
  6. embedded mode on by default             -> rc 1
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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(text).lstrip())


def build(tmp, *, with_cognee=True, entry_point=True, backend="HttpBackend",
          embedded_default=False, needs_llm_key=False):
    site = os.path.join(tmp, "site")
    pkg = os.path.join(site, "cognee_integration_hermes")

    if with_cognee:
        write(os.path.join(site, "cognee", "__init__.py"), "")

    write(os.path.join(pkg, "__init__.py"),
          "from .provider import CogneeMemoryProvider\n")
    write(os.path.join(pkg, "backend.py"), """
        import importlib.util
        def has_cognee():
            return importlib.util.find_spec("cognee") is not None
        class HttpBackend: pass
        class SdkBackend: pass
        def build_backend(config=None):
            return %s()
        """ % backend)
    write(os.path.join(pkg, "config.py"), """
        import os
        def load_config(home=None):
            return {
                "service_url": os.environ.get("COGNEE_BASE_URL", ""),
                "llm_api_key": os.environ.get("LLM_API_KEY", ""),
                "embedded": %s,
            }
        """ % ("True" if embedded_default
               else 'os.environ.get("COGNEE_EMBEDDED", "") == "true"'))
    # Upstream's gate, verbatim in shape: package first, then the mode.
    gate = ('bool(cfg.get("llm_api_key"))' if needs_llm_key
            else 'bool(cfg.get("service_url") or cfg.get("llm_api_key"))')
    write(os.path.join(pkg, "provider.py"), """
        from .backend import has_cognee
        from .config import load_config
        class CogneeMemoryProvider:
            def is_available(self):
                if not has_cognee():
                    return False
                cfg = load_config()
                return %s
        """ % gate)

    dist = os.path.join(site, "cognee_integration_hermes-1.2.2.dist-info")
    write(os.path.join(dist, "METADATA"),
          "Metadata-Version: 2.1\nName: cognee-integration-hermes-agent\n"
          "Version: 1.2.2\n")
    if entry_point:
        write(os.path.join(dist, "entry_points.txt"),
              "[hermes_agent.memory_providers]\n"
              "cognee = cognee_integration_hermes\n")
    return site


def run(site):
    env = dict(os.environ)
    env["PYTHONPATH"] = site
    for k in ("COGNEE_BASE_URL", "COGNEE_EMBEDDED", "COGNEE_TRANSPORT",
              "LLM_API_KEY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, SMOKE], env=env,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def case(title, expect_rc, needle, **kw):
    print("\n" + title)
    tmp = tempfile.mkdtemp(prefix="cognee-smoke-")
    try:
        rc, out = run(build(tmp, **kw))
        check("exits %d" % expect_rc, rc == expect_rc, out[:170])
        check("output names %r" % needle, needle in out, out[:220])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


case("1/6 cognee importable + cloud config passes", 0,
     "is_available() True")
case("2/6 cognee NOT importable -- the #31 regression -- fails", 1,
     "has_cognee() is False", with_cognee=False)
case("3/6 missing entry point (1.0.0-1.2.0 shape) fails", 1,
     "#382", entry_point=False)
case("4/6 is_available() False despite cognee present fails", 1,
     "is_available() is False", needs_llm_key=True)
case("5/6 cloud config selecting SdkBackend fails", 1,
     "SdkBackend", backend="SdkBackend")
case("6/6 embedded-by-default fails", 1,
     "embedded mode is on by default", embedded_default=True)

print("")
if failures:
    sys.exit("FAILED: %s" % failures)
print("all cognee-cloud smoke tests passed")

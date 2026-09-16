#!/usr/bin/env python3
"""Build-time guard for the --no-deps Cognee Cloud install.

We install cognee-integration-hermes-agent WITHOUT its `cognee==1.5.x` pin,
because in cloud/remote mode the plugin never touches that package: it talks to
the tenant over REST through `http_backend.HttpBackend`, which imports only
stdlib. Skipping the pin avoids ~64 transitive packages (litellm, pyarrow,
pylance, rdflib, redis, ...) and sidesteps the base image's
`exclude-newer = "14 days"` quarantine entirely -- the reason the previous
cognee block was removed in ccd1b05da.

That saving is only safe while the cloud path stays cognee-free, so assert it
here rather than discover it in a 3am container start:

  1. the pip entry point registers in `hermes_agent.memory_providers` -- the
     group plugins/memory/__init__.py actually scans (their issue #382; fixed
     in 1.2.1, so a downgrade below that silently yields an ignored plugin)
  2. importing the package needs no third-party module -- proven by importing
     it with `cognee` masked as unimportable
  3. a cloud-shaped config (COGNEE_BASE_URL set, COGNEE_EMBEDDED unset) selects
     HttpBackend, not SdkBackend

If a future release moves a `cognee` import to module scope or flips the
default transport, this fails the BUILD. The fix is then either to drop
--no-deps (and take the exclude-newer exemption) or to pin back.
"""
import sys

TAG = "[cognee-cloud]"


def main():
    # 2. Make `cognee` unimportable for the whole check, so any module-scope
    # dependency on it surfaces as an ImportError right here.
    # find_spec, not the find_module/load_module pair: the legacy finder API was
    # removed in 3.12, so on this image's 3.13 a legacy blocker is never consulted
    # and the mask would silently pass against a package that DOES import cognee.
    # find_spec, not the find_module/load_module pair: the legacy finder API was
    # removed in 3.12, so on this image's 3.13 a legacy blocker is never consulted
    # and the mask would silently pass against a package that DOES import cognee.
    class _Blocker:
        def __init__(self, names):
            self.names = tuple(names)

        def find_spec(self, name, path=None, target=None):
            if any(name == n or name.startswith(n + ".") for n in self.names):
                raise ImportError("%s %s is deliberately masked" % (TAG, name))
            return None

    # Control. cognee is NOT installed here, so "import cognee fails" would hold
    # whether or not the mask works -- a vacuous check. Prove the mask bites by
    # pointing it at a module that provably exists and is not yet imported.
    canary = "colorsys"
    if canary in sys.modules:
        sys.exit("%s FATAL: control module %s already imported" % (TAG, canary))
    sys.meta_path.insert(0, _Blocker([canary]))
    try:
        __import__(canary)
    except ImportError:
        pass
    else:
        sys.exit("%s FATAL: the import mask is not in effect (%s imported through "
                 "it), so this check cannot prove the cloud path is cognee-free."
                 % (TAG, canary))
    finally:
        sys.meta_path.pop(0)

    sys.meta_path.insert(0, _Blocker(["cognee"]))

    import importlib.metadata as md

    eps = [e for e in md.entry_points().select(group="hermes_agent.memory_providers")]
    names = sorted(e.name for e in eps)
    if "cognee" not in names:
        sys.exit("%s FATAL: no 'cognee' entry point in hermes_agent.memory_providers "
                 "(found %s). Below 1.2.1 the plugin installs but is silently "
                 "ignored -- see their issue #382." % (TAG, names or "none"))

    try:
        import cognee_integration_hermes as pkg
    except ImportError as exc:
        sys.exit("%s FATAL: the plugin does not import without cognee (%s). The "
                 "cloud path has gained a module-scope dependency on the pinned "
                 "package, so --no-deps is no longer safe: either drop it and add "
                 "`--exclude-newer-package cognee=<date>`, or pin back." % (TAG, exc))

    if not hasattr(pkg, "CogneeMemoryProvider"):
        sys.exit("%s FATAL: CogneeMemoryProvider missing from the package" % TAG)

    # 3. Cloud shape: a service URL selects remote mode; embedded stays off.
    import os

    os.environ["COGNEE_BASE_URL"] = "https://smoke.invalid"
    os.environ.pop("COGNEE_EMBEDDED", None)
    os.environ.pop("COGNEE_TRANSPORT", None)

    from cognee_integration_hermes import backend, config

    cfg = config.load_config("/nonexistent-cognee-smoke")
    if not cfg.get("service_url"):
        sys.exit("%s FATAL: COGNEE_BASE_URL did not select remote mode" % TAG)
    if cfg.get("embedded"):
        sys.exit("%s FATAL: embedded mode is on by default; --no-deps is unsafe" % TAG)

    chosen = type(backend.build_backend(cfg)).__name__
    if chosen != "HttpBackend":
        sys.exit("%s FATAL: cloud config selected %s, not HttpBackend. That path "
                 "needs the cognee package, so --no-deps is unsafe." % (TAG, chosen))

    print("%s ok: entry point registered, imports are stdlib-only, "
          "cloud config -> HttpBackend" % TAG)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build-time guard for the Cognee Cloud memory provider (dinefile profile).

History, because it explains the odd shape of this file. PR #31 installed the
plugin with --no-deps, reasoning that cloud mode never imports the pinned
`cognee` package: every module in `cognee_integration_hermes` is stdlib-only and
a set COGNEE_BASE_URL selects `http_backend.HttpBackend`, a urllib REST client.
All of that is true, and all of it was beside the point --

    def is_available(self) -> bool:
        if not has_cognee():            # find_spec("cognee") is not None
            return False
        cfg = load_config()
        return bool(cfg.get("service_url") or cfg.get("llm_api_key"))

-- because `is_available()` gates on the package being importable BEFORE it
looks at the mode. The plugin installed, imported and configured correctly, and
Hermes reported "Status: not available" forever. The old version of this file
asserted the import worked and the backend was HttpBackend; it never called the
one method that decides whether the provider is usable.

So: the pin is back (with an exclude-newer exemption, see the Dockerfile), and
this checks the gate itself rather than its ingredients.

  1. the pip entry point registers in `hermes_agent.memory_providers` -- the
     group plugins/memory/__init__.py actually scans (upstream #382; fixed in
     1.2.1, so a downgrade below that silently yields an ignored plugin)
  2. `cognee` is importable, so has_cognee() is True -- the --no-deps regression
  3. `CogneeMemoryProvider().is_available()` is True under a cloud-shaped
     environment -- what Hermes asks before using the provider at all
  4. that same config still selects HttpBackend, not SdkBackend -- the pin is
     there to satisfy (2), not to put the heavy in-process SDK on the per-turn
     request path
"""
import os
import sys

TAG = "[cognee-cloud]"


def main():
    import importlib.metadata as md

    # 1. Discoverable to Hermes' own loader.
    names = sorted(e.name for e in
                   md.entry_points().select(group="hermes_agent.memory_providers"))
    if "cognee" not in names:
        sys.exit("%s FATAL: no 'cognee' entry point in hermes_agent.memory_providers "
                 "(found %s). Below 1.2.1 the plugin installs but is silently "
                 "ignored -- see upstream issue #382." % (TAG, names or "none"))

    # Cloud shape, set before importing the plugin: load_config() reads the
    # environment at call time, and is_available() calls it with no arguments.
    os.environ["COGNEE_BASE_URL"] = "https://smoke.invalid"
    os.environ.pop("COGNEE_EMBEDDED", None)
    os.environ.pop("COGNEE_TRANSPORT", None)
    os.environ.pop("LLM_API_KEY", None)

    from cognee_integration_hermes import backend, config
    from cognee_integration_hermes.provider import CogneeMemoryProvider

    # 2. The gate's first condition. This is the check whose absence shipped a
    # provider that could never be used.
    if not backend.has_cognee():
        sys.exit("%s FATAL: has_cognee() is False -- the cognee package is not "
                 "importable. is_available() short-circuits on this BEFORE it "
                 "looks at the mode, so the provider would install, import and "
                 "configure correctly and still never be used. Do not install "
                 "this plugin with --no-deps." % TAG)

    cfg = config.load_config("/nonexistent-cognee-smoke")
    if not cfg.get("service_url"):
        sys.exit("%s FATAL: COGNEE_BASE_URL did not select remote mode" % TAG)
    if cfg.get("embedded"):
        sys.exit("%s FATAL: embedded mode is on by default; this image has no "
                 "local server story" % TAG)

    # 3. The gate itself, with no LLM_API_KEY set -- cloud mode must not need one.
    if not CogneeMemoryProvider().is_available():
        sys.exit("%s FATAL: is_available() is False under a cloud-shaped config "
                 "(COGNEE_BASE_URL set, LLM_API_KEY unset). Hermes will report "
                 "'Status: not available' and never use the provider." % TAG)

    # 4. Keep the in-process SDK off the per-turn path.
    chosen = type(backend.build_backend(cfg)).__name__
    if chosen != "HttpBackend":
        sys.exit("%s FATAL: cloud config selected %s, not HttpBackend. The pinned "
                 "package is here to satisfy has_cognee(), not to serve requests "
                 "in-process." % (TAG, chosen))

    print("%s ok: entry point registered, has_cognee() True, is_available() True "
          "without an LLM key, cloud config -> HttpBackend" % TAG)
    return 0


if __name__ == "__main__":
    sys.exit(main())

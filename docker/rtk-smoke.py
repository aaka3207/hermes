#!/usr/bin/env python3
"""Build-time guard for the RTK terminal-command rewriter.

RTK only pays for itself if FOUR independent things hold at once, and three of
them fail silently -- the agent keeps working, it just quietly reads full command
output forever and nobody notices. So assert each one here, at build time:

  1. `shutil.which("rtk")` finds the binary. This is literally what the plugin's
     register() calls; when it returns None the plugin prints one warning to
     stderr and skips hook registration entirely. In gateway/service mode that
     warning goes to a log nobody reads. (Upstream hit exactly this: see
     NousResearch/hermes-agent#44582, where the reporter closed with "will
     ensure rtk is in the agent process PATH when running from gateway mode".)

  2. It is the right `rtk`. Two unrelated projects ship a binary by that name --
     Rust Token Killer (this one) and Rust Type Kit. A `rewrite` subcommand that
     actually rewrites is the discriminator, so this calls it and checks the
     output rather than trusting `--version`.

  3. Hermes' own discovery accepts the bundled plugin. `scan_directory` parses
     the manifest and `gate_manifest` decides its fate; this runs both, against
     the real /opt/hermes/plugins tree, and requires kind/key/gate to come out
     as "loads once enabled". It also asserts the OPPOSITE case -- that with no
     allow-list the plugin is skipped -- because that is the step an operator
     must still perform by hand on each profile's config.yaml, and a future
     upstream change making standalone plugins auto-load should be noticed here
     rather than inferred from a mystery behavior change.

  4. The adapter itself rewrites a terminal payload in place, and leaves every
     other shape alone. This is the end-to-end behavior the whole integration
     exists for, minus Hermes' dispatch (verified upstream in #44582: the same
     args dict object reaches the tool, in both the sequential and concurrent
     tool-execution paths).

Run by the Dockerfile with the image's venv python, which has `hermes_cli` on
its path via the editable install at /opt/hermes.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

TAG = "[rtk]"

PLUGIN_NAME = "rtk-rewrite"

# The plugin treats these as "rtk made a decision"; 1 and 2 mean "left alone".
ACCEPTED_REWRITE_RETURN_CODES = {0, 3}


def fatal(message):
    sys.exit("%s FATAL: %s" % (TAG, message))


def check_binary_on_path():
    """1 + 2: the binary register() looks for, and it being the RTK we mean."""
    found = shutil.which("rtk")
    if not found:
        fatal("rtk is not on PATH (%s). The plugin calls shutil.which('rtk') in "
              "register() and silently skips hook registration when it is absent, "
              "so the image would ship a plugin that never runs."
              % os.environ.get("PATH", "<unset>"))

    probe = "cat /etc/hostname"
    try:
        result = subprocess.run(["rtk", "rewrite", probe], capture_output=True,
                                text=True, timeout=10)
    except OSError as exc:
        fatal("could not execute %s: %s" % (found, exc))
    except subprocess.TimeoutExpired:
        fatal("`rtk rewrite` did not return within 10s")

    if result.returncode not in ACCEPTED_REWRITE_RETURN_CODES:
        fatal("`rtk rewrite %r` exited %d (stderr: %s). Exit 1/2 mean 'no rewrite', "
              "which for a plain `cat` means this is not Rust Token Killer -- check "
              "that the release URL still points at rtk-ai/rtk."
              % (probe, result.returncode, result.stderr.strip() or "<empty>"))

    rewritten = result.stdout.strip()
    if not rewritten or rewritten == probe:
        fatal("`rtk rewrite %r` returned %r -- no rewrite happened, so nothing "
              "would ever be compressed." % (probe, rewritten))
    print("%s ok: %s rewrites %r -> %r" % (TAG, found, probe, rewritten))
    return rewritten


def check_discovery():
    """3: upstream's own manifest scan + gate, run against the real plugins tree."""
    try:
        from hermes_cli.plugins import get_bundled_plugins_dir
        from hermes_cli.plugins_discovery import gate_manifest, scan_directory
    except ImportError as exc:
        fatal("cannot import Hermes' plugin discovery (%s). Without it this check "
              "cannot prove the plugin is loadable." % exc)

    bundled = get_bundled_plugins_dir()
    # Same exclusions the real sweep uses for the top-level pass; those
    # categories have their own discovery (see collect_directory_manifests).
    manifests = scan_directory(
        bundled, "bundled",
        skip_names={"memory", "context_engine", "platforms", "model-providers"},
    )
    mine = [m for m in manifests if m.name == PLUGIN_NAME]
    if not mine:
        fatal("Hermes' scan of %s found no %r manifest (saw: %s). The plugin dir "
              "or its plugin.yaml is missing or unparseable."
              % (bundled, PLUGIN_NAME, sorted(m.name for m in manifests) or "none"))
    manifest = mine[0]

    if manifest.source != "bundled":
        fatal("%r resolved as source=%r, expected 'bundled'"
              % (PLUGIN_NAME, manifest.source))

    if "pre_tool_call" not in manifest.provides_hooks:
        fatal("the manifest declares hooks %s, not pre_tool_call. Hermes surfaces "
              "provides_hooks to operators; a drift here means the adapter and its "
              "manifest have parted ways." % (manifest.provides_hooks or "none"))

    enabled_gate = gate_manifest(manifest, set(), {PLUGIN_NAME})
    if enabled_gate.action != "load":
        fatal("with %r in plugins.enabled the gate still says %r (%s). The plugin "
              "would be discovered and never loaded."
              % (PLUGIN_NAME, enabled_gate.action, enabled_gate.error or "no reason"))

    # The opt-in step is load-bearing operator knowledge; assert it is still real.
    unenabled_gate = gate_manifest(manifest, set(), None)
    if unenabled_gate.action == "load":
        print("%s note: this plugin now loads WITHOUT a plugins.enabled entry "
              "(upstream gating changed). docs/rtk-command-rewrite.md says an "
              "operator must enable it per profile -- update that doc." % TAG)
    print("%s ok: discovered as kind=%s key=%s at %s; gates to 'load' when enabled"
          % (TAG, manifest.kind, manifest.key, manifest.path))
    return manifest


def load_adapter(manifest):
    """Import the exact file Hermes' own discovery resolved, not a guessed path."""
    init_py = os.path.join(str(manifest.path), "__init__.py")
    if not os.path.isfile(init_py):
        fatal("%s is missing; `rtk init --agent hermes` did not produce the adapter"
              % init_py)
    spec = importlib.util.spec_from_file_location("_rtk_rewrite_smoke", init_py)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        fatal("the plugin adapter does not import: %s" % exc)
    return module


def check_adapter(module, expected_rewrite):
    """4: the hook registers, rewrites terminal payloads, and fails open."""
    registered = {}

    class Ctx:
        def register_hook(self, name, callback):
            registered[name] = callback

    module.register(Ctx())
    callback = registered.get("pre_tool_call")
    if callback is None:
        fatal("register() did not register a pre_tool_call hook (registered: %s). "
              "The usual cause is rtk not being on PATH at this point."
              % (sorted(registered) or "nothing"))

    args = {"command": "cat /etc/hostname"}
    callback(tool_name="terminal", args=args)
    if args["command"] != expected_rewrite:
        fatal("the hook left the terminal command as %r; expected %r. The adapter "
              "and the binary disagree about the rewrite contract."
              % (args["command"], expected_rewrite))

    # Fail-open shapes: anything that is not a terminal command must pass through
    # untouched rather than raise into Hermes' tool dispatch.
    other = {"command": "cat /etc/hostname"}
    callback(tool_name="read_file", args=other)
    if other["command"] != "cat /etc/hostname":
        fatal("the hook rewrote a non-terminal tool payload")
    for payload in (None, {}, {"command": ""}, {"command": 42}):
        try:
            callback(tool_name="terminal", args=payload)
        except Exception as exc:
            fatal("the hook raised on payload %r (%s); it must fail open"
                  % (payload, exc))

    print("%s ok: pre_tool_call hook registered, rewrites terminal commands in "
          "place, passes everything else through" % TAG)


def main():
    rewritten = check_binary_on_path()
    manifest = check_discovery()
    check_adapter(load_adapter(manifest), rewritten)
    print("%s ok: rtk installed, plugin bundled and loadable. Enable it per "
          "profile with `hermes plugins enable %s`." % (TAG, PLUGIN_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tests for docker/rtk-smoke.py (the RTK build guard).

Pure stdlib, no image dependencies -- runs anywhere:

    python3 tests/test_rtk_smoke.py

Everything the smoke script touches is faked in a temp dir: a shell-script
`rtk` on PATH, a `hermes_cli` package exposing the two discovery functions the
script calls, and a plugin directory holding a stand-in adapter with the same
fail-open contract as the real one. The real script then runs against that.

Each guard here corresponds to a way this integration breaks SILENTLY in
production -- the agent stays healthy and simply stops saving tokens:

  1. everything wired                      -> rc 0
  2. rtk not on PATH                       -> rc 1  (hook never registers)
  3. rtk present but never rewrites        -> rc 1  (wrong `rtk`, or a dud build)
  4. plugin missing from the bundled dir   -> rc 1  (installer layout drift)
  5. manifest gated off even when enabled  -> rc 1  (e.g. kind change upstream)
  6. adapter registers no pre_tool_call    -> rc 1  (rtk absent at register time)
  7. adapter raises on an odd payload      -> rc 1  (fail-open regression)
  8. adapter's rewrite disagrees with rtk  -> rc 1  (contract drift)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

sys.stdout.reconfigure(line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMOKE = os.path.join(ROOT, "docker", "rtk-smoke.py")

failures = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(text).lstrip())
    os.chmod(path, mode)


# A stand-in for the vendored adapter. Same contract as rtk-ai/rtk's
# hooks/hermes/rtk-rewrite/__init__.py: register only when rtk is on PATH, only
# touch terminal payloads, never raise.
ADAPTER = '''
    import shutil
    import subprocess

    ACCEPTED = {0, 3}


    def register(ctx):
        if shutil.which("rtk") is None:
            return
        ctx.register_hook("pre_tool_call", _pre_tool_call)


    def _pre_tool_call(tool_name=None, args=None, **_kwargs):
        try:
            if tool_name != "terminal" or not isinstance(args, dict):
                return
            command = args.get("command")
            if not isinstance(command, str) or not command.strip():
                return
            result = subprocess.run(["rtk", "rewrite", command],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode not in ACCEPTED:
                return
            rewritten = result.stdout.strip()
            if rewritten and rewritten != command:
                args["command"] = rewritten
        except Exception:
            return
'''

# Minimal stand-in for Hermes' discovery: enough shape for the smoke script's
# calls, with the one behavior that matters -- plugins.enabled decides the gate.
HERMES_DISCOVERY = '''
    import os
    from dataclasses import dataclass, field
    from typing import List, Optional


    @dataclass
    class PluginManifest:
        name: str
        source: str
        path: str
        kind: str = "standalone"
        key: str = ""
        provides_hooks: List[str] = field(default_factory=list)


    @dataclass
    class ManifestGate:
        action: str
        enabled: bool = False
        error: Optional[str] = None
        log: Optional[tuple] = None


    def _read(path, key):
        for line in open(path):
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip().strip('"')
        return ""


    def scan_directory(path, source, skip_names=None, prefix="", depth=0):
        out = []
        if not os.path.isdir(path):
            return out
        for child in sorted(os.listdir(path)):
            full = os.path.join(str(path), child)
            manifest = os.path.join(full, "plugin.yaml")
            if not os.path.isfile(manifest):
                continue
            hooks = [l.strip()[2:] for l in open(manifest)
                     if l.startswith("  - ")]
            out.append(PluginManifest(
                name=_read(manifest, "name") or child, source=source, path=full,
                kind=_read(manifest, "kind") or "standalone", key=child,
                provides_hooks=hooks,
            ))
        return out


    def gate_manifest(manifest, disabled, enabled):
        names = {manifest.key, manifest.name}
        if names & disabled:
            return ManifestGate("placeholder", error="disabled via config")
        if manifest.kind == "exclusive":
            return ManifestGate("placeholder", error="exclusive plugin")
        if enabled is None or not names & enabled:
            return ManifestGate("placeholder", error="not enabled in config")
        return ManifestGate("load")
'''


def build(tmp, *, rtk="rewriting", plugin=True, kind="standalone",
          adapter=ADAPTER, hooks="  - pre_tool_call"):
    """Lay out a fake image slice and return (env, plugins_dir)."""
    bindir = os.path.join(tmp, "bin")
    site = os.path.join(tmp, "site")
    plugins = os.path.join(tmp, "plugins")
    os.makedirs(bindir, exist_ok=True)
    os.makedirs(plugins, exist_ok=True)

    if rtk == "rewriting":
        # `rtk rewrite "cat X"` -> "rtk read X", exit 0, like the real binary.
        write(os.path.join(bindir, "rtk"), '''
            #!/bin/sh
            [ "$1" = "rewrite" ] || { echo "rtk 0.0.0-fake"; exit 0; }
            echo "$2" | sed 's/^cat /rtk read /'
            exit 0
        ''', mode=0o755)
    elif rtk == "inert":
        # Present, but declines every command -- the "wrong rtk" shape.
        write(os.path.join(bindir, "rtk"), '''
            #!/bin/sh
            [ "$1" = "rewrite" ] || { echo "rtk 0.0.0-fake"; exit 0; }
            exit 1
        ''', mode=0o755)
    elif rtk == "echoing":
        # Returns the command unchanged with an accepted exit code.
        write(os.path.join(bindir, "rtk"), '''
            #!/bin/sh
            [ "$1" = "rewrite" ] || { echo "rtk 0.0.0-fake"; exit 0; }
            echo "$2"
            exit 0
        ''', mode=0o755)

    if plugin:
        pdir = os.path.join(plugins, "rtk-rewrite")
        write(os.path.join(pdir, "plugin.yaml"),
              'name: rtk-rewrite\nversion: "0.1.0"\nkind: %s\nprovides_hooks:\n%s\n'
              % (kind, hooks))
        write(os.path.join(pdir, "__init__.py"), adapter)

    write(os.path.join(site, "hermes_cli", "__init__.py"), "\n")
    write(os.path.join(site, "hermes_cli", "plugins.py"),
          "from pathlib import Path\n"
          "def get_bundled_plugins_dir():\n"
          "    return Path(%r)\n" % plugins)
    write(os.path.join(site, "hermes_cli", "plugins_discovery.py"), HERMES_DISCOVERY)

    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
    env["PYTHONPATH"] = site
    return env


def run(env):
    return subprocess.run([sys.executable, SMOKE], env=env,
                          capture_output=True, text=True, timeout=60)


def case(name, expect_rc, **kwargs):
    tmp = tempfile.mkdtemp(prefix="rtk-smoke-test-")
    try:
        result = run(build(tmp, **kwargs))
        ok = result.returncode == expect_rc
        detail = "rc=%d (wanted %d): %s" % (
            result.returncode, expect_rc,
            (result.stderr.strip() or result.stdout.strip()).splitlines()[-1:] or "")
        check(name, ok, "" if ok else detail)
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("rtk smoke-script guards:")

    # 1. The control: everything wired the way the image wires it.
    result = case("wired correctly -> rc 0", 0)
    ok = "rtk read" in result.stdout
    check("  reports the rewrite it verified", ok,
          "" if ok else result.stdout.strip()[-160:])

    # 2. The failure upstream actually hit (hermes-agent#44582): the binary is
    # absent from the agent's PATH, so register() silently skips the hook.
    case("rtk missing from PATH -> rc 1", 1, rtk=None)

    # 3. A binary named rtk that rewrites nothing -- the other project by that
    # name, or a build with no filters compiled in.
    case("rtk never rewrites (exit 1) -> rc 1", 1, rtk="inert")
    case("rtk echoes the command back -> rc 1", 1, rtk="echoing")

    # 4. rtk init stopped producing the plugin where we look for it.
    case("plugin absent from bundled dir -> rc 1", 1, plugin=False)

    # 5. Upstream reclassifies the plugin so it can never load.
    case("manifest gated off despite being enabled -> rc 1", 1, kind="exclusive")

    # 5b. Manifest and adapter drift apart.
    case("manifest declares no pre_tool_call -> rc 1", 1, hooks="  - post_tool_call")

    # 6. An adapter that registers nothing -- how the real one behaves when rtk
    # is missing, and how a broken rewrite of it would behave always.
    case("adapter registers no hook -> rc 1", 1, adapter='''
        def register(ctx):
            return
    ''')

    # 7. Fail-open regression: the hook must never raise into tool dispatch.
    case("adapter raises on an odd payload -> rc 1", 1, adapter='''
        def register(ctx):
            ctx.register_hook("pre_tool_call", _pre_tool_call)


        def _pre_tool_call(tool_name=None, args=None, **_kwargs):
            if tool_name == "terminal":
                args["command"] = "rtk read /etc/hostname"
    ''')

    # 8. The adapter agrees it should rewrite, but not on what -- e.g. a stale
    # vendored copy against a newer binary.
    case("adapter rewrite disagrees with rtk -> rc 1", 1, adapter='''
        def register(ctx):
            ctx.register_hook("pre_tool_call", _pre_tool_call)


        def _pre_tool_call(tool_name=None, args=None, **_kwargs):
            try:
                if tool_name == "terminal" and isinstance(args, dict):
                    args["command"] = "rtk cat /etc/hostname"
            except Exception:
                return
    ''')

    print("")
    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("all rtk smoke-script guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

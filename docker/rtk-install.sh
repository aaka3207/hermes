#!/bin/sh
# Install RTK (https://github.com/rtk-ai/rtk) and its Hermes plugin into the image.
#
# RTK rewrites terminal commands to compact equivalents (`cat X` -> `rtk read X`,
# `git status` -> `rtk git status`) so the agent reads far fewer output bytes per
# tool call. The Hermes integration is a thin Python plugin that registers a
# `pre_tool_call` hook, shells out to `rtk rewrite <command>`, and mutates the
# terminal tool's `command` in place. All rewrite logic stays in the Rust binary.
#
# TWO ARTIFACTS, ONE PLACE. Upstream's documented install is `rtk init --agent
# hermes`, which writes the plugin into $HERMES_HOME/plugins/ AND patches
# $HERMES_HOME/config.yaml. $HERMES_HOME here is /opt/data -- the runtime volume,
# which the build cannot see and which each profile owns a separate copy of. So
# this script runs that same installer against a THROWAWAY HERMES_HOME and keeps
# only the plugin, relocating it to the image's bundled plugin dir
# (/opt/hermes/plugins/rtk-rewrite).
#
# Why bundled rather than on the volume:
#   * hermes_cli/plugins.py scans bundled `<install>/plugins/<name>/` before the
#     user dir, so a bundled plugin is discovered identically -- verified by
#     running upstream's own scan_directory()/gate_manifest() against this exact
#     plugin.yaml: kind=standalone, gate=load once enabled.
#   * both profiles (default + dinefile) are separate HERMES_HOMEs with separate
#     gateways; one bundled copy serves both, a volume copy would need two.
#   * nothing mutates the volume at boot, and the plugin version is pinned by the
#     image rather than by whenever someone last ran `rtk init` in a shell.
#
# What this deliberately does NOT do is enable the plugin. `plugins.enabled` is an
# opt-in allow-list in each profile's config.yaml on the volume (gate_manifest:
# bundled auto-load applies only to kind=backend/platform, not standalone), and
# that file is operator-owned. See docs/rtk-command-rewrite.md for the one-time
# `hermes plugins enable rtk-rewrite` step.
#
# Fail-closed throughout: a bad download, a checksum mismatch, an installer that
# stops producing the plugin, or an unknown CPU architecture all abort the build.
set -eu

RTK_VERSION="v0.49.0"

# From the release's own checksums.txt, which is itself published per tag:
#   https://github.com/rtk-ai/rtk/releases/download/v0.49.0/checksums.txt
# Pinned here rather than fetched so the build cannot be handed a different
# binary by a re-tagged release. Bump both the version and the hashes together.
SHA_X86_64="7278231dfd7e6a730a4ab7f847b195bcf02289c2d57622b0dab75a6411100c8f"
SHA_AARCH64="c8ea4b6560841e73157c134fd4a3293914c6ede42e786ee985cf491fde691ba7"

BIN_DIR="/usr/local/bin"
PLUGIN_DIR="/opt/hermes/plugins/rtk-rewrite"

# uname -m, not $TARGETARCH: Coolify builds with the classic builder, where
# TARGETARCH is unset unless explicitly declared and passed. The build platform
# is the target platform here, so uname is both correct and always populated.
case "$(uname -m)" in
    x86_64|amd64)
        # musl: statically linked, so it does not care about the base image's libc.
        TARGET="x86_64-unknown-linux-musl"
        SHA="$SHA_X86_64"
        ;;
    aarch64|arm64)
        TARGET="aarch64-unknown-linux-gnu"
        SHA="$SHA_AARCH64"
        ;;
    *)
        echo "[rtk] FATAL: unsupported architecture $(uname -m); rtk publishes x86_64 and aarch64 only" >&2
        exit 1
        ;;
esac

TARBALL="rtk-${TARGET}.tar.gz"
URL="https://github.com/rtk-ai/rtk/releases/download/${RTK_VERSION}/${TARBALL}"
TMP="$(mktemp -d)"
# shellcheck disable=SC2064  # expand TMP now, not at trap time.
trap "rm -rf '$TMP'" EXIT INT TERM

echo "[rtk] downloading ${RTK_VERSION} for ${TARGET}"
curl -fsSL "$URL" -o "${TMP}/${TARBALL}"

echo "${SHA}  ${TMP}/${TARBALL}" | sha256sum -c - >/dev/null || {
    echo "[rtk] FATAL: checksum mismatch for ${TARBALL}" >&2
    echo "[rtk]        expected ${SHA}" >&2
    echo "[rtk]        got      $(sha256sum "${TMP}/${TARBALL}" | cut -d' ' -f1)" >&2
    exit 1
}

tar -xzf "${TMP}/${TARBALL}" -C "$TMP"
install -m 0755 "${TMP}/rtk" "${BIN_DIR}/rtk"
echo "[rtk] installed $("${BIN_DIR}/rtk" --version) to ${BIN_DIR}/rtk"

# Generate the plugin with the installed binary, so the adapter always matches
# the rtk version it shells out to (the installer embeds the plugin source at
# compile time). The throwaway home absorbs the config.yaml it also writes.
FAKE_HOME="${TMP}/hermes-home"
mkdir -p "$FAKE_HOME"
HERMES_HOME="$FAKE_HOME" "${BIN_DIR}/rtk" init --agent hermes >/dev/null

SRC="${FAKE_HOME}/plugins/rtk-rewrite"
for f in __init__.py plugin.yaml; do
    [ -f "${SRC}/${f}" ] || {
        echo "[rtk] FATAL: 'rtk init --agent hermes' did not write ${f}." >&2
        echo "[rtk]        The installer's layout changed; re-check the plugin path" >&2
        echo "[rtk]        before bumping RTK_VERSION." >&2
        exit 1
    }
done

rm -rf "$PLUGIN_DIR"
mkdir -p "$(dirname "$PLUGIN_DIR")"
cp -a "$SRC" "$PLUGIN_DIR"
# The gateway runs as uid 10000 and only ever reads this; keep it world-readable
# and owned by root so no runtime context can rewrite the adapter.
chmod 0755 "$PLUGIN_DIR"
chmod 0644 "${PLUGIN_DIR}/__init__.py" "${PLUGIN_DIR}/plugin.yaml"
echo "[rtk] bundled plugin installed at ${PLUGIN_DIR}"

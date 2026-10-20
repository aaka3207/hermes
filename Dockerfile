# Pinned by digest, not :latest. This is v0.22.2 (the image `:latest`/`:stable`/
# `:v0.22.2` all pointed at on 2026-10-19; manifest-list digest, linux/amd64 +
# arm64). It replaces the 2026-10-14 v0.22.0 pin sha256:c69536a39e88..., which
# itself replaced the 2026-10-08 pin sha256:9774f4f39a9b... (tagged
# rc.4-v0.21.6), where upstream's PM rework first shipped and broke several
# things we had to work around (see
# docs/hermes-pm-base-image-update-2026-10-09.md). Bump this deliberately,
# after a test build, not by letting :latest move under us.
FROM nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919

USER root
# The base image stopped putting uv on PATH (it now lives under
# /opt/hermes/tools/uv-<version>-linux-x64/), so ship our own. Likewise npm's
# default global prefix is now PM's hash-verified node dir (installing there
# trips `hermes pm doctor`, and its bin/ is not on PATH), so the global CLIs
# below go to /usr/local instead. npm 12 also runs no install scripts by default
# (allow-scripts is empty), which leaves claude-code's stub claude.exe unreplaced
# ("native binary not installed"), so its postinstall is run explicitly.
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
RUN npm install -g --prefix /usr/local @anthropic-ai/claude-code byterover-cli ntn @upstash/cli vercel @posthog/cli supabase && npm cache clean --force && \
    node /usr/local/lib/node_modules/@anthropic-ai/claude-code/install.cjs && \
    uv pip install --python /opt/hermes/.venv/bin/python --no-cache hindsight-client==0.6.1 faster-whisper==1.2.1

# The Cognee memory provider (cloud mode, dinefile profile) installs
# near the end of this file instead of here: it needs docker/cognee-cloud-smoke.py,
# and sharing the one COPY there keeps this early, cache-stable region untouched
# when that script changes. Search for "cognee-cloud".

# Patch the openai SDK's streaming Responses parser to tolerate a null
# `response.output`. The ChatGPT Codex backend (chatgpt.com/backend-api/codex,
# used by provider openai-codex) streams events with output=None, which
# violates the OpenAI API contract and crashes the SDK at
# `_parsing/_responses.py: for output in response.output` -> TypeError:
# 'NoneType' object is not iterable. This breaks ALL Codex calls (agent chat
# and auxiliary/memory LLM calls). The buggy line is identical in every openai
# release through 2.38.0, so no version bump fixes it — the guard is the fix.
# Idempotent + non-fatal: warns (doesn't fail the build) if the anchor is gone,
# so this no-ops cleanly once openai/NousResearch ship a real upstream fix —
# at which point this whole RUN block should be removed.
# UPSTREAM STATUS: NousResearch/hermes-agent#34544 merged on main 2026-05-29
# but NOT in v0.15.2. Remove this block once v0.15.3 (or whichever release
# includes #34544) is in `nousresearch/hermes-agent:latest`.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import openai.lib._parsing._responses as m
f = m.__file__
s = open(f).read()
old = "    for output in response.output:"
new = "    for output in (response.output or []):"
if new in s:
    print("openai null-guard already present:", f)
elif s.count(old) == 1:
    open(f, "w").write(s.replace(old, new))
    print("patched openai null-guard:", f)
else:
    print("WARNING: openai parse_response anchor not found (count=%d); "
          "skipping Codex null-guard patch — review if Codex breaks." % s.count(old))
PY

# Fix Photon sidecar ERR_MODULE_NOT_FOUND on managed images (read-only
# /opt/hermes). sidecar_paths.py mirrors the sidecar source into the writable
# data volume (/opt/data/photon/sidecar) when the install tree is read-only,
# but its _MIRROR_FILES tuple drifts: upstream keeps adding .mjs modules that
# index.mjs imports without adding them to the mirror list, so the mirrored
# copy silently lacks them and the sidecar crashes at startup with
# `Cannot find module '.../send-format.mjs'`.
#
# HISTORY / WHY THIS IS NOT A STRING ANCHOR ANYMORE. The original version of
# this patch matched the literal multi-line tuple and appended two hardcoded
# filenames. On 2026-09-14 upstream reflowed that tuple onto ONE line; the
# anchor stopped matching, the patch warn-and-skipped, and the build still
# succeeded -- silently reintroducing the very crash it exists to prevent.
# (Confirmed in a real build AND in the then-running production image.) The bug
# was masked only because the volume still held the two .mjs files copied by an
# older, correctly-patched image.
#
# So this rewrite checks the INVARIANT instead of the TEXT: every local
# `./*.mjs` module that index.mjs imports must appear in _MIRROR_FILES. That
# survives reformatting, picks up newly added sidecar modules automatically,
# and -- importantly -- becomes a clean no-op if upstream ever fixes the list
# themselves, so it does not fail the build over a bug that no longer exists.
#
# FAIL-CLOSED. Exits non-zero (failing the image build) if either file is
# missing, if the tuple cannot be parsed, if index.mjs yields no local imports
# (which would mean this parser has gone stale), if the rewritten source does
# not compile, or if the invariant still does not hold afterwards. Naturally
# idempotent: a second run finds nothing missing and no-ops.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import os, re, sys

BASE = "/opt/hermes/plugins/platforms/photon"
SP = os.path.join(BASE, "sidecar_paths.py")
IDX = os.path.join(BASE, "sidecar", "index.mjs")

absent = [p for p in (SP, IDX) if not os.path.exists(p)]
if absent:
    sys.exit("FATAL: photon mirror patch: required file(s) missing: %s" % absent)

TUPLE_RE = re.compile(r"_MIRROR_FILES\s*=\s*\(([^)]*)\)", re.S)

def mirror_list(text):
    m = TUPLE_RE.search(text)
    if not m:
        return None, None
    return m, re.findall(r"[\"']([^\"']+)[\"']", m.group(1))

src = open(SP).read()
m, current = mirror_list(src)
if m is None:
    sys.exit("FATAL: photon mirror patch: _MIRROR_FILES tuple not found in %s" % SP)
if not current:
    sys.exit("FATAL: photon mirror patch: _MIRROR_FILES parsed as empty in %s" % SP)

# Every local sibling module index.mjs pulls in must be mirrored alongside it.
imports = sorted(set(re.findall(
    r"""from\s*["']\./([A-Za-z0-9._-]+\.mjs)["']""", open(IDX).read())))
if not imports:
    sys.exit("FATAL: photon mirror patch: no local .mjs imports parsed from %s "
             "(import syntax changed? this parser is stale)" % IDX)

missing = [name for name in imports if name not in current]
if not missing:
    print("photon _MIRROR_FILES already covers every index.mjs import:", imports)
else:
    merged = current + missing
    repl = ("_MIRROR_FILES = (%s)  # hermes-patch: derived from index.mjs imports"
            % ", ".join('"%s"' % n for n in merged))
    out = src[:m.start()] + repl + src[m.end():]
    try:
        compile(out, SP, "exec")
    except SyntaxError as e:
        sys.exit("FATAL: photon mirror patch would produce invalid source: %s" % e)
    with open(SP, "w") as fh:
        fh.write(out)
    print("patched photon _MIRROR_FILES: added %s" % missing)

# Verify FUNCTIONALLY (import the module, read the real attribute), not by
# re-parsing our own edit.
import importlib.util as u
spec = u.spec_from_file_location("_hermes_sidecar_paths_check", SP)
mod = u.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    sys.exit("FATAL: photon mirror patch: sidecar_paths.py no longer imports: %s" % e)
final = list(getattr(mod, "_MIRROR_FILES", ()))
still = [n for n in imports if n not in final]
if still:
    sys.exit("FATAL: photon mirror invariant violated; still missing %s (have %s)"
             % (still, final))
print("photon mirror invariant OK -- every index.mjs local import is mirrored:", imports)
print("  _MIRROR_FILES =", final)
PY

# ---------------------------------------------------------------------------
# CONSOLIDATED PATCH GATE. Fails the build unless every compatibility patch is
# actually in effect.
#
# WHY THIS EXISTS. Patch 1 is anchor-based and warn-rather-than-fail: if
# upstream renames or reformats the code it targets, it skips and the build
# still succeeds, shipping a silently degraded image. That is not theoretical --
# on 2026-09-14 the photon patch died exactly that way (upstream reflowed a
# tuple onto one line) and reached production unnoticed. The base image is
# `nousresearch/hermes-agent:latest`, deliberately unpinned because upstream
# ships constantly, so every rebuild can change the code underneath all of them.
#
# DESIGN RULE: assert INVARIANTS, not anchors. An upstream *fix* must PASS here,
# not false-alarm -- otherwise this gate becomes noise and gets disabled. Each
# check below asks "is the bad behavior absent?", never "is my exact string
# present?". Prefer a functional check (call it, observe the result) over a
# source check wherever one is available.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import importlib.util as u, os, re, sys

SP = "/opt/hermes/.venv/lib/python3.13/site-packages"
failures = []

def check(label, ok, detail=""):
    print("  %-4s %-56s %s" % ("ok" if ok else "FAIL", label, detail))
    if not ok:
        failures.append(label)

# --- patch 1: openai streams output=None on the Codex backend --------------
# Invariant: no unguarded iteration over response.output survives anywhere in
# the installed openai package.
unguarded, guarded = [], []
for root, _dirs, files in os.walk(os.path.join(SP, "openai")):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if re.search(r"for\s+output\s+in\s+response\.output\s*:", text):
            unguarded.append(path)
        if "for output in (response.output or []):" in text:
            guarded.append(path)
compat = bool(guarded) or not unguarded
check("patch1 no unguarded response.output iteration", not unguarded,
      "%d unguarded" % len(unguarded))
check("patch1 guard present OR upstream no longer iterates", compat,
      "%d guarded site(s)" % len(guarded))

# --- patch 5b already self-gates, re-assert cheaply -------------------
sp_mod = "/opt/hermes/plugins/platforms/photon/sidecar_paths.py"
idx = "/opt/hermes/plugins/platforms/photon/sidecar/index.mjs"
if os.path.exists(sp_mod) and os.path.exists(idx):
    spec = u.spec_from_file_location("_gate_sidecar_paths", sp_mod)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mirrored = list(getattr(mod, "_MIRROR_FILES", ()))
    needed = sorted(set(re.findall(
        r"""from\s*["']\./([A-Za-z0-9._-]+\.mjs)["']""", open(idx).read())))
    check("patch5b every index.mjs import is mirrored",
          all(n in mirrored for n in needed), "%d needed" % len(needed))
else:
    check("patch5b photon files present", False)

print("")
if failures:
    sys.exit("FATAL: compatibility patch gate failed: %s" % failures)
print("compatibility patch gate PASSED -- all patches verified in effect")
PY

# Make the CLI reachable from login shells:
#
# Login shells (`bash -l`, some web terminals): /etc/profile resets PATH
#    to the Debian default, dropping /opt/hermes/bin (the exec shim) and the
#    venv — "command not found" is what pushes operators to type the venv
#    path and bypass the shim. Restore the image PATH via profile.d.
RUN printf '%s\n' \
        '# hermes-agent: restore image PATH in login shells (profile resets it)' \
        'export PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:$PATH"' \
        > /etc/profile.d/99-hermes-path.sh

# ---------------------------------------------------------------------------
# hermes-lcm #588 mitigation — SQLite POSIX-lock drop.
#
# hermes-lcm's artifact-permission helpers do open(2) -> fchmod(2) -> close(2)
# on lcm.db and its -wal/-shm sidecars. Closing ANY descriptor to a file drops
# that process's POSIX advisory locks on it (https://sqlite.org/howtocorrupt.html),
# which silently released SQLite's locks — including the -shm DMS lock that
# stops another process unlinking the live WAL. A short-lived `hermes` CLI
# against the same HERMES_HOME then replaced the WAL/SHM pair while the
# long-lived gateway and dashboard kept writing through the deleted inodes.
#
# Observed here: every child-agent spawn fell back to the built-in compressor
# ("could not be safely copied ... disk I/O error") 28 minutes after a clean
# install, and /opt/data/lcm.db was malformed 90 minutes after that. Confirmed
# on this host: 15 deleted lcm.db-wal/-shm handles held by the gateway and
# dashboard, and ZERO POSIX locks on any lcm.db.
#   upstream: https://github.com/stephenschoettler/hermes-lcm/issues/588
#   our data: that issue, comment 5684122223
#
# Unlike every patch above, this one canNOT run at build time: hermes-lcm is
# not in this image. It installs into $HERMES_HOME/plugins on the runtime
# volume, so the build cannot see it, and a hand-patch there is silently
# reverted by the next plugin update. The entrypoint re-applies it on every
# container start instead. Logic, exit codes and rationale live in
# docker/lcm-588-mitigation.py; tests in tests/test_lcm_588_mitigation.py.
COPY docker/lcm-588-mitigation.py docker/entrypoint-lcm-guard.sh \
     docker/cognee-cloud-smoke.py docker/cognee-remember-filename.py \
     /opt/hermes/docker/
RUN chmod +x /opt/hermes/docker/lcm-588-mitigation.py \
             /opt/hermes/docker/entrypoint-lcm-guard.sh \
             /opt/hermes/docker/cognee-remember-filename.py

# Smoke-check the copied script so a broken edit fails the BUILD rather than a
# 3am container start: it must compile, and the absent-plugin branch must exit
# 0 (a host with no LCM installed must still boot). The behavioural guarantee —
# that an already-private artifact is never opened — is covered by
# tests/test_lcm_588_mitigation.py, which runs on plain stdlib anywhere.
RUN /opt/hermes/.venv/bin/python -m py_compile \
        /opt/hermes/docker/lcm-588-mitigation.py && \
    HERMES_HOME=/nonexistent-lcm-smoke \
        /opt/hermes/.venv/bin/python /opt/hermes/docker/lcm-588-mitigation.py && \
    echo "lcm-588 boot patcher: compiles, absent-plugin branch exits 0"

# ---------------------------------------------------------------- cognee-cloud
# Cognee — graph-backed memory provider, CLOUD mode. Active on the `dinefile`
# profile (memory.provider: cognee in that profile's config.yaml); the default
# profile uses openviking. Profiles are separate HERMES_HOMEs with separate
# gateway processes, so the two never share a store.
#
# The cognee pin is required even though cloud mode never calls into it:
#
#     def is_available(self) -> bool:
#         if not has_cognee():          # find_spec("cognee") is not None
#             return False
#         cfg = load_config()
#         return bool(cfg.get("service_url") or cfg.get("llm_api_key"))
#
# is_available() short-circuits on the package BEFORE it looks at the mode, and
# Hermes asks that before using a provider at all. #31 installed this --no-deps
# on the (correct, irrelevant) grounds that the cloud path is stdlib-only; the
# provider installed, imported and configured fine and reported "Status: not
# available" forever. docker/cognee-cloud-smoke.py now asserts the gate itself.
#
# That puts us back on the exclude-newer wall that removed the first cognee
# block in ccd1b05da, and adds a second, harder one. Two constraints decide the
# version here, and 1.2.1 -- what #31 pinned -- satisfies neither:
#
#   1. cognee's own dependency chain must not fight hermes-agent's pins.
#      cognee==1.5.3 (what 1.2.1 requires) needs limits>=4.4.1,<5, which needs
#      packaging>=21,<25 -- while hermes-agent exact-pins packaging==26.0. uv
#      resolves that by silently DOWNGRADING packaging 26.0 -> 24.2 under the
#      host application's own pin. Asking for both is simply unsatisfiable:
#          "we can conclude that cognee-integration-hermes-agent==1.2.1
#           depends on packaging>=21,<25 ... your requirements are unsatisfiable"
#      cognee 1.5.4 moves to limits==5.8.0, which lifts the cap. So 1.2.2 is not
#      a nice-to-have here, it is the first version that coexists with the image.
#
#   2. cognee 1.6.0 shipped 2026-09-18, inside the base image's
#      `exclude-newer = "14 days"` (/opt/hermes/pyproject.toml), so a plain
#      resolve fails with "there is no version of cognee==1.6.0". The exemption
#      below is scoped to that one package and dated one day past its release --
#      the shape the base pyproject already documents as safe ("Exempting exact
#      pins is pure brick-risk removal at no supply-chain cost"). cognee==1.6.0
#      is an exact pin held by the integration, so the cutoff adds no float
#      protection here, only brick risk.
#
#      The date moved 09-05 -> 09-19 with the plugin bump below. 1.3.0 pins
#      cognee==1.6.0, which is also the version the self-hosted backend runs
#      (deploy/cognee-selfhost.compose.yaml) -- the plugin and the server now
#      agree, which they had not since the backend moved to 1.6.0.
#
#      **One exemption is not enough: exempt the transitive exact pins too.**
#      cognee==1.6.0 depends on `enola-cli==0.4.21`, released 2026-09-18, also
#      inside the window -- so exempting `cognee` alone still fails, and the
#      message names enola-cli rather than cognee, which is easy to misread as
#      a broken dependency:
#          "Because there is no version of enola-cli==0.4.21 and cognee==1.6.0
#           depends on enola-cli==0.4.21, we can conclude that cognee==1.6.0
#           cannot be used."
#      Any exact pin cognee holds on a package released inside the window needs
#      its own line. Verified by a dry-run resolve against a simulated 14-day
#      wall before this landed: 143 packages, cognee 1.6.0, limits 5.8.0,
#      packaging 26.0 -- no downgrade, so guard (3) below still holds.
#
# Deliberately NOT `--exclude-newer-package cognee=$(date)`: a fixed date means a
# future bump to a newer cognee fails this build loudly instead of silently
# widening the window. Bump the pins together, or not at all.
#
# `packaging==26.0` is passed as a resolver guard, not because anything here
# wants it: it makes constraint (1) self-enforcing, so if a future cognee
# re-introduces the cap the build FAILS rather than quietly downgrading a
# package hermes-agent exact-pins. Verified: with it, 64 installs, zero
# removals, zero downgrades; openai and pydantic-core untouched, so the Codex
# null-guard above still holds.
#
# Installed from a FORK, not from PyPI and not from upstream. The fork is
# `aaka3207/cognee-integrations`, pinned to a full commit sha on its `main`
# (PRs #1-#4 squash-merged, then `7343d4f` pushed directly). Pin the merged commit, not the topic branch's tip: the
# squash orphans that tip, and an orphaned sha is fetchable only until GitHub
# garbage-collects it. The subtree was compared before repinning -- the squashed
# `integrations/hermes-agent` tree is byte-identical to the branch tip's.
#
# The fork is upstream `main` (plugin 1.3.0) plus these changes, none of which
# changes behaviour until a config key or tool argument is set (except 1, a bug
# fix):
#
#   1. `_remember` derived its upload filename from the content instead of
#      uploading every memory as `memory.txt`. That fixed name makes cognee
#      >= 1.6.0 reject every write after the first with a 409 while recall keeps
#      working -- the silent write outage of 2026-09-21..23. This replaces the
#      in-image patch that `docker/cognee-remember-filename.py` used to apply;
#      see the note below the RUN for why that script is still here.
#   2. `search_type` became a config key. It had existed only as a per-call tool
#      argument, so an agent that omitted it fell through to the server's query
#      classifier, which routes a short query to an LLM completion over the
#      graph -- and a completion paraphrases names, ids and dates rather than
#      returning the record. Measured: the query that returned the right record
#      ranked first under CHUNKS came back through the classifier as a request
#      to resend a link. Set `search_type` in the profile's cognee.json.
#   3. `session_writes` became a config key. `sync_turn` mirrors every turn into
#      the session cache from a background thread and had no switch;
#      `improve_on_end: false` looks like one and is not -- it governs only
#      promotion at session end. Verified live: both session tables truncated to
#      zero refilled within five minutes with improve_on_end already off.
#   4. The `GRAPH_COMPLETION` literal behind `auto_route: false` got a name.
#      No behaviour change; it documents that turning auto_route off pins the
#      completion rather than making retrieval more literal.
#   5. `write_metadata` (fork PRs #2 and #3). When it is on, every permanent
#      write carries cognee `external_metadata`: created_at, created_by
#      (`created_by` key, default "hermes"), write_origin and
#      hermes_session_id. `cognee_remember` also accepts an optional flat
#      `metadata` object, validated before anything is stored. On cognee
#      >= 1.6.1 (topoteretes/cognee#5158) that metadata lands on the chunks,
#      and `cognee_recall` returns it with each CHUNKS result. The only
#      fork-only part is `notion_pointer.py`: a `notion_page_id` key must hold
#      a real page id or the write is refused. Everything else is generic.
#   6. `context_only` on `cognee_recall` (fork PR #4). It sends
#      `only_context=True` with GRAPH_COMPLETION (or the completion type the
#      caller names), so the tool returns the graph context the server's LLM
#      would have answered from, and the agent writes the answer. No answer
#      is generated, so none is saved to the session. Verified on 1.6.1:
#      cache_qa_entries did not move.
#   7. `memory_write_targets` (fork `7343d4f`). `on_memory_write` copies every
#      built-in memory add/replace into the dataset; this names which targets
#      to copy. Set to ["user"] so USER.md facts reach the shared store and
#      MEMORY.md, the agent's notes on its own behaviour, does not -- Desktop
#      recalled a Hermes behaviour rule as a fact about the user.
#
# Return to PyPI when a release carries 1-7. They are separable and intended to
# go upstream, except `notion_pointer.py`; commit 1 is
# `topoteretes/cognee-integrations#436`.
# Until then: rebase the fork onto upstream rather than cherry-picking, and move
# the sha here in the same commit as the `--exclude-newer-package` date if the
# rebase brings a new cognee pin with it.
#
# None of the 64 packages reaches the per-turn path: a set COGNEE_BASE_URL
# selects http_backend.HttpBackend, a urllib REST client. The smoke check
# asserts that, and asserts is_available() -- see that file for why.
#
# 1.2.1 is still the floor for discovery: 1.0.0-1.2.0 declared only the generic
# `hermes_agent.plugins` group, which plugins/memory/__init__.py never reads, so
# those installed a provider Hermes silently ignored (upstream #382). No shim
# dir is needed -- this base image does scan `hermes_agent.memory_providers`
# (plugins/memory/__init__.py:30), unlike the image ccd1b05da was written for.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
        --exclude-newer-package "cognee=2026-09-19T00:00:00Z" \
        --exclude-newer-package "enola-cli=2026-09-19T00:00:00Z" \
        "cognee-integration-hermes-agent @ git+https://github.com/aaka3207/cognee-integrations.git@7343d4f10b549622c4ff3688e7ef7a4691af579b#subdirectory=integrations/hermes-agent" \
        "packaging==26.0" && \
    /opt/hermes/.venv/bin/python /opt/hermes/docker/cognee-cloud-smoke.py && \
    /opt/hermes/.venv/bin/python /opt/hermes/docker/cognee-remember-filename.py

# The line above is now a GUARD, not a patch. The fork pinned above carries the
# fix in its own source (commit 1), so the script finds nothing to patch and
# asserts instead.
#
# What it is guarding against: `_remember` used to upload every memory as a file
# named `memory.txt`. Harmless on cognee 1.5.4, where add() silently replaced a
# same-named document -- fatal against the self-hosted cognee >= 1.6.0 backend
# (deploy/cognee-selfhost.compose.yaml), which raises
# DocumentUpdateRequiredError (409) instead. The first remember in a dataset then
# wins and every later one fails, while recall and the Claude Desktop MCP path
# keep working, so memory looks healthy and stores nothing. Measured: two days of
# silent write loss, 2026-09-21 to 2026-09-23.
#
# The script asserts the installed `_remember` derives its upload name from the
# content and names no fixed file, and exits 1 -- failing the build -- if either
# is false. That is what makes a bad fork rebase loud: drop commit 1 while
# syncing upstream and the build stops here instead of shipping another silent
# write outage. It still applies the patch itself if it meets the old anchor, so
# a rollback to the upstream pin needs no change here.
#
# Chained into the install's own RUN deliberately: as a separate layer it could
# be cached while the install layer beneath it rebuilt, leaving a fresh
# unguarded plugin under a cached pass.
#
# Upstream: topoteretes/cognee-integrations#436. Delete this note,
# docker/cognee-remember-filename.py and
# tests/test_cognee_remember_filename.py once that lands upstream AND the pin
# above returns to a PyPI release carrying it.

# --------------------------------------------------------------------- rtk
# RTK — rewrites the agent's terminal commands into compact equivalents
# (`cat x` -> `rtk read x`, `git status` -> `rtk git status`) so each tool call
# returns far fewer bytes for the model to read. Two artifacts: the pinned Rust
# binary on PATH, and a thin Python adapter registered as a `pre_tool_call`
# hook that shells out to `rtk rewrite` and mutates the terminal tool's
# `command` in place. Every rewrite decision stays in the binary.
#
# Both are installed into the IMAGE, not the runtime volume, even though
# upstream's documented `rtk init --agent hermes` targets $HERMES_HOME. That
# command writes the plugin under $HERMES_HOME/plugins and patches
# $HERMES_HOME/config.yaml — here $HERMES_HOME is /opt/data, a volume the build
# cannot see and which each profile owns its own copy of. docker/rtk-install.sh
# runs the same installer against a throwaway home and keeps only the plugin,
# relocating it to the bundled plugin dir that hermes_cli/plugins.py scans
# first. One copy then serves both the default and dinefile gateways, nothing
# mutates the volume at boot, and the plugin can never drift from the binary
# it delegates to.
#
# STILL NEEDS ONE MANUAL STEP. `plugins.enabled` in each profile's config.yaml
# is an opt-in allow-list, and bundled auto-load applies only to backend and
# platform plugins — this one is kind=standalone, so it is discovered and left
# dormant until enabled. That file lives on the volume and is operator-owned:
#     hermes plugins enable rtk-rewrite     # once per profile, then restart
# See docs/rtk-command-rewrite.md.
#
# The smoke check is not ceremony: three of the four ways this breaks are
# silent (binary off the agent's PATH -> hook never registers; manifest not
# accepted -> plugin never loads; adapter/binary contract drift -> no rewrite),
# and each one leaves a perfectly healthy agent that simply reads full output
# forever. See docker/rtk-smoke.py for what each check defends.
COPY docker/rtk-install.sh docker/rtk-smoke.py /opt/hermes/docker/
RUN chmod +x /opt/hermes/docker/rtk-install.sh && \
    /opt/hermes/docker/rtk-install.sh && \
    /opt/hermes/.venv/bin/python /opt/hermes/docker/rtk-smoke.py

# rtk's usage ping already requires explicit consent (unset by default) and the
# published builds compile in no collector URL, so this is belt-and-braces: the
# agent's own commands never phone home even if a future build ships one.
ENV RTK_TELEMETRY_DISABLED=1

# Wrap the stock entrypoint so the mitigation runs before anything opens the
# LCM database. Declaring ENTRYPOINT resets the base image's CMD, so re-declare
# it — docker-compose.yaml passes `command: gateway run` for this service, so
# the CMD here only matters for a bare `docker run`.
#
# Scope: only the `hermes` service builds from this Dockerfile, and it is the
# only container that mounts the volume at /opt/data. Both processes that
# corrupted the database (`hermes gateway run` and `hermes dashboard`) live in
# it, so this guard covers the affected HERMES_HOME. hermes_webui runs a
# different image (ghcr.io/nesquena/hermes-webui) and has its own unpatched LCM
# under /home/hermeswebui/.hermes — independent, container-local, and not
# shared with this volume, so it cannot corrupt /opt/data/lcm.db.
ENTRYPOINT ["/opt/hermes/docker/entrypoint-lcm-guard.sh"]
CMD ["gateway", "run"]

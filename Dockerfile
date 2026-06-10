FROM nousresearch/hermes-agent:latest

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends syncthing && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code byterover-cli && npm cache clean --force && \
    uv pip install --python /opt/hermes/.venv/bin/python --no-cache hindsight-client==0.6.1 faster-whisper==1.2.1

# Mnemosyne — local-first SQLite memory provider (alternative to hindsight).
# Installed as a *bundled* provider: pip-install the engine, then symlink the
# provider package into the image's bundled memory-plugins dir. This avoids the
# upstream installer's $HERMES_HOME/plugins volume symlink, which points at a
# python-version-specific site-packages path and dangles if the base image
# bumps Python. Memory DB lives on the volume via MNEMOSYNE_DATA_DIR (compose).
# Activate by setting `memory.provider: mnemosyne` in config.yaml.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "mnemosyne-memory[embeddings]==3.5.0" sqlite-vec==0.1.9 && \
    ln -s "$(/opt/hermes/.venv/bin/python -c 'import importlib.util as u; print(u.find_spec("hermes_memory_provider").submodule_search_locations[0])')" \
        /opt/hermes/plugins/memory/mnemosyne && \
    /opt/hermes/.venv/bin/python -c "import importlib.util as u; assert u.find_spec('mnemosyne'), 'mnemosyne import failed'; print('mnemosyne provider linked OK')"

# Patch the openai SDK's streaming Responses parser to tolerate a null
# `response.output`. The ChatGPT Codex backend (chatgpt.com/backend-api/codex,
# used by provider openai-codex) streams events with output=None, which
# violates the OpenAI API contract and crashes the SDK at
# `_parsing/_responses.py: for output in response.output` -> TypeError:
# 'NoneType' object is not iterable. This breaks ALL Codex calls (agent chat
# AND mnemosyne memory ops). The buggy line is identical in every openai
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

# Patch Mnemosyne to lazily (re)register the Hermes/Codex host LLM backend.
# The provider registers it in initialize(), but the live gateway doesn't keep
# it registered for the consolidation/extraction paths (module-identity /
# lifecycle quirk), so memory ops silently fall back to non-LLM "AAAK"
# summaries + regex facts. This makes the host-backend gate self-heal: if no
# backend is registered when an LLM call is attempted, register it on the spot
# (register_hermes_host_llm is idempotent). Result: consolidation + fact
# extraction use Codex via Hermes' auxiliary client instead of AAAK/regex.
# Idempotent + non-fatal; remove if/when Mnemosyne fixes gateway registration.
# Verified against mnemosyne-memory==3.5.0: bug still unfixed upstream
# (core/llm_backends.py byte-identical 3.1.2->3.5.0); both anchors match
# (count=1 each, local_llm.py L293-294 + L325-327).
RUN /opt/hermes/.venv/bin/python - <<'PY'
import mnemosyne.core.local_llm as m
f = m.__file__
s = open(f).read()
if "register_hermes_host_llm()" in s:
    print("mnemosyne lazy-register already present:", f)
else:
    old1 = ("        from mnemosyne.core.llm_backends import get_host_llm_backend\n"
            "        return get_host_llm_backend() is not None\n")
    new1 = ("        from mnemosyne.core.llm_backends import get_host_llm_backend\n"
            "        if get_host_llm_backend() is None:\n"
            "            try:\n"
            "                from hermes_memory_provider.hermes_llm_adapter import register_hermes_host_llm\n"
            "                register_hermes_host_llm()\n"
            "            except Exception:\n"
            "                pass\n"
            "        return get_host_llm_backend() is not None\n")
    old2 = ("    if get_host_llm_backend() is None:\n"
            "        return (False, None)\n"
            "    raw = call_host_llm(\n")
    new2 = ("    if get_host_llm_backend() is None:\n"
            "        try:\n"
            "            from hermes_memory_provider.hermes_llm_adapter import register_hermes_host_llm\n"
            "            register_hermes_host_llm()\n"
            "        except Exception:\n"
            "            pass\n"
            "    if get_host_llm_backend() is None:\n"
            "        return (False, None)\n"
            "    raw = call_host_llm(\n")
    if s.count(old1) == 1 and s.count(old2) == 1:
        open(f, "w").write(s.replace(old1, new1).replace(old2, new2))
        print("patched mnemosyne lazy-register OK:", f)
    else:
        print("WARNING: mnemosyne anchors not found (%d/%d); skipping." % (s.count(old1), s.count(old2)))
PY

# Persist the mnemosyne embedding-model cache for root-context CLI runs.
# mnemosyne hardcodes the fastembed cache to ~/.hermes/cache/fastembed
# (embeddings.py; no env override — MNEMOSYNE_DATA_DIR only moves the DB).
# The gateway and the PATH shim (/opt/hermes/bin/hermes) run with
# HOME=/opt/data, so their cache lands on the volume; but invoking the venv
# binary directly as root (`docker exec … /opt/hermes/.venv/bin/hermes`, as
# older upstream docs suggest) gets HOME=/root and re-downloads the ~65MB
# model into the ephemeral layer after every redeploy. Symlink root's cache
# dir onto the volume so every context shares the persistent copy (dangles
# at build time; resolves once the volume is mounted — mnemosyne's makedirs
# follows it). The rm also drops the stray ~/.hermes/mnemosyne DB the patch
# step above bakes into the image (importing mnemosyne as root initializes
# a DB at build time).
RUN rm -rf /root/.hermes && mkdir -p /root/.hermes && \
    ln -s /opt/data/.hermes/cache /root/.hermes/cache

# Make the CLI reachable + safe from every in-container shell context:
#
# 1) Login shells (`bash -l`, some web terminals): /etc/profile resets PATH
#    to the Debian default, dropping /opt/hermes/bin (the exec shim) and the
#    venv — "command not found" is what pushes operators to type the venv
#    path and bypass the shim. Restore the image PATH via profile.d.
#
# 2) The `mnemosyne` entrypoint: upstream only shims `hermes`. Running
#    `mnemosyne ...` as root gets HOME=/root (ephemeral cache, root-owned
#    files). Mirror the upstream shim: drop to the hermes user with
#    HOME=/opt/data; pass through unchanged when already non-root.
RUN printf '%s\n' \
        '# hermes-agent: restore image PATH in login shells (profile resets it)' \
        'export PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:$PATH"' \
        > /etc/profile.d/99-hermes-path.sh && \
    printf '%s\n' \
        '#!/bin/sh' \
        '# docker-exec privilege-drop shim for the mnemosyne CLI; mirrors' \
        '# /opt/hermes/bin/hermes (see hermes-exec-shim.sh for rationale).' \
        'REAL=/opt/hermes/.venv/bin/mnemosyne' \
        '[ -x "$REAL" ] || { echo "mnemosyne-shim: $REAL missing" >&2; exit 127; }' \
        'if [ "$(id -u)" != "0" ]; then exec "$REAL" "$@"; fi' \
        'export HOME=/opt/data' \
        'exec /command/s6-setuidgid hermes "$REAL" "$@"' \
        > /opt/hermes/bin/mnemosyne && \
    chmod +x /opt/hermes/bin/mnemosyne

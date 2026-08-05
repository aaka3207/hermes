FROM nousresearch/hermes-agent:latest

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends syncthing && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code byterover-cli ntn && npm cache clean --force && \
    uv pip install --python /opt/hermes/.venv/bin/python --no-cache hindsight-client==0.6.1 faster-whisper==1.2.1

# Mnemosyne — local-first SQLite memory provider (alternative to hindsight).
# Installed as a *bundled* provider: pip-install the engine, then symlink the
# provider package into the image's bundled memory-plugins dir. This avoids the
# upstream installer's $HERMES_HOME/plugins volume symlink, which points at a
# python-version-specific site-packages path and dangles if the base image
# bumps Python. Memory DB lives on the volume via MNEMOSYNE_DATA_DIR (compose),
# at the SAME host path the standalone mnemosyne-mcp Coolify service (separate
# repo) points its own MNEMOSYNE_DATA_DIR at — the two processes share one
# SQLite file so hermes (in-process, auto-injected memory) and MCP clients like
# Claude Desktop (tool-call driven, via mnemosyne-mcp) see the same memories.
# Embeddings are routed through OpenRouter's /embeddings API (compose env vars
# MNEMOSYNE_EMBEDDINGS_VIA_API / OPENROUTER_BASE_URL / OPENROUTER_API_KEY /
# MNEMOSYNE_EMBEDDING_MODEL / MNEMOSYNE_EMBEDDING_DIM) instead of local
# fastembed ONNX inference — the [embeddings] extra (which bundles fastembed)
# is deliberately omitted, keeping embedding generation off this container's
# CPU/RAM budget entirely; sqlite-vec still does the local similarity search
# over the resulting vectors. Activate by setting `memory.provider: mnemosyne`
# in config.yaml.
#
# mnemosyne-memory ships NO plugin.yaml anywhere in the package (verified by
# installing it and inspecting site-packages) — hermes-agent's directory-based
# plugin discovery explicitly skips any plugins/memory/<name>/ dir lacking one
# ("no plugin.yaml, depth cap reached"), so without this the provider silently
# never registers (symptom: `hermes doctor` / `hermes memory doctor` reports
# "mnemosyne plugin not found" despite __init__.py + register() being present
# and importable). Same class of gap already solved for cognee above via its
# hand-authored shim dir; mnemosyne instead symlinks straight into the
# discovery dir, so the manifest is written into the real site-packages
# directory the symlink resolves to.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "mnemosyne-memory==3.5.0" sqlite-vec==0.1.9 && \
    PKGDIR="$(/opt/hermes/.venv/bin/python -c 'import importlib.util as u; print(u.find_spec("hermes_memory_provider").submodule_search_locations[0])')" && \
    printf '%s\n' \
        'name: mnemosyne' \
        'version: 3.5.0' \
        'description: "Mnemosyne — local-first SQLite memory provider with vector + FTS5 hybrid search."' \
        'pip_dependencies: []' \
        'requires_env: []' \
        'hooks:' \
        '  - system_prompt_block' \
        '  - prefetch' \
        '  - queue_prefetch' \
        '  - sync_turn' \
        '  - on_session_end' \
        '  - on_memory_write' \
        '  - shutdown' \
        > "$PKGDIR/plugin.yaml" && \
    ln -s "$PKGDIR" /opt/hermes/plugins/memory/mnemosyne && \
    /opt/hermes/.venv/bin/python -c "import importlib.util as u; assert u.find_spec('mnemosyne'), 'mnemosyne import failed'; print('mnemosyne provider linked OK')" && \
    test -f /opt/hermes/plugins/memory/mnemosyne/plugin.yaml && \
    echo "mnemosyne plugin.yaml present OK"

# Cognee — graph-based memory provider (cloud/remote mode). Installed from the
# upstream integrations monorepo subdirectory (not published to PyPI). The pip
# package exposes CogneeMemoryProvider (a MemoryProvider subclass) + a
# register(ctx) hook via the `hermes_agent.plugins` entry point. BUT this pinned
# base image predates entry-point discovery — it finds memory providers by
# scanning /opt/hermes/plugins/memory/<name>/ for an __init__.py (+ optional
# plugin.yaml), the same dir-based mechanism mnemosyne was symlinked into. So a
# bare pip install leaves cognee in site-packages where discovery never looks
# (it won't appear in `hermes memory` and memory.provider: cognee won't resolve).
# We therefore also drop a small bundled shim dir that re-exports the installed
# provider, making it discoverable. Activate via `memory.provider: cognee` in
# config.yaml; cloud mode is driven by COGNEE_BASE_URL/COGNEE_API_KEY (Coolify,
# passed through compose). Heavy dep tree (cognee>=1.0.0 pulls
# litellm/lancedb/pyarrow/etc.); resolves cleanly with no openai/pydantic-core
# changes (verified), so the Codex null-guard below is unaffected.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
        "cognee-integration-hermes-agent @ git+https://github.com/topoteretes/cognee-integrations.git#subdirectory=integrations/hermes-agent" && \
    /opt/hermes/.venv/bin/python -c "import importlib.util as u; assert u.find_spec('cognee_integration_hermes'), 'cognee integration import failed'; print('cognee provider installed OK')" && \
    mkdir -p /opt/hermes/plugins/memory/cognee && \
    printf '%s\n' \
        '"""Bundled shim: expose the pip-installed cognee provider to dir-based memory discovery."""' \
        'from cognee_integration_hermes import CogneeMemoryProvider, register  # noqa: F401' \
        > /opt/hermes/plugins/memory/cognee/__init__.py && \
    printf '%s\n' \
        'name: cognee' \
        'version: 0.1.0' \
        'description: "Cognee — graph-based memory (cloud/remote mode)."' \
        'pip_dependencies: []' \
        'requires_env: []' \
        'hooks:' \
        '  - on_session_end' \
        > /opt/hermes/plugins/memory/cognee/plugin.yaml && \
    test -f /opt/hermes/plugins/memory/cognee/__init__.py && \
    test -f /opt/hermes/plugins/memory/cognee/plugin.yaml && \
    echo "cognee provider plugin dir created OK"

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
# REQUIRED for MNEMOSYNE_EXTRACTION_PROMPT (set in Coolify) to have any effect:
# without a live LLM backend, extract_facts() returns [] and the custom prompt
# is never consulted, so stored memories degrade to raw/AAAK junk.
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

# Disable Mnemosyne's UNCONDITIONAL regex fact extractor
# (BeamMemory.extract_and_store_facts, beam.py). mnemosyne runs TWO extractors on
# every write/consolidation: the LLM one (_extract_and_store_facts -> extraction.py,
# governed by MNEMOSYNE_EXTRACTION_PROMPT) which we keep, AND a hardcoded
# multilingual regex extractor that scrapes "first/then" sequences, named dates, and
# imperative sentences ("never call X", "must first Y") into memoria_facts/instructions.
# The regex layer has NO config gate in 3.5.0 (ignore_patterns/sync_roles/the prompt
# don't reach it — confirmed against source + docs); even upstream main only hides its
# output at recall, still writing the junk. Neutralize it by overriding the method with
# a no-op (returns the empty per-type counts dict its callers expect). The LLM
# extractor + preferences are unaffected. Idempotent; drop this once on a version that
# gates the extractor at write time.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import mnemosyne.core.beam as m
f = m.__file__
s = open(f).read()
marker = "# hermes-patch: disable regex fact extractor"
if marker in s:
    print("regex-extractor patch already present:", f)
else:
    s += ("\n\n" + marker + "\n"
          "try:\n"
          "    BeamMemory.extract_and_store_facts = lambda self, *a, **k: {}\n"
          "except Exception as _e:\n"
          "    print('WARNING: could not disable regex extractor:', _e)\n")
    open(f, "w").write(s)
    print("patched: regex fact extractor disabled:", f)
PY

# Wire author attribution (MNEMOSYNE_AUTHOR_ID/_TYPE) into the LIVE hermes
# plugin package `hermes_memory_provider` (installed alongside mnemosyne-memory
# as its host adapter — NOT the similarly-named `mnemosyne_hermes` tool-schema
# package bundled under site-packages/integrations/hermes/src, which is unused
# here and only matters for the standalone mnemosyne-mcp server).
#
# Two independent changes, because mnemosyne's own design couples them:
#   1. Stamp writes: BeamMemory() is constructed with author_id=None, so every
#      remember()/remember_batch()/consolidate() call (all read self.author_id
#      internally) stores author_id=NULL. Pass the env vars at construction so
#      the shared DB can distinguish hermes's writes from Claude Desktop's
#      (mnemosyne-mcp, author_id=claude-desktop) for self-audit.
#   2. Keep automatic recall shared: _prefetch_bank() reads
#      `self._beam.author_id or os.environ.get("MNEMOSYNE_AUTHOR_ID")` and, if
#      truthy, passes author_id into beam.recall() — which per beam.py's own
#      comment triggers a `(1=1)` clause that filters to that author AND skips
#      session/channel scoping. Once change #1 makes self._beam.author_id
#      non-None, every automatic per-turn recall would silently narrow to only
#      hermes-authored memories, INCLUDING the 88 existing rows all being
#      author_id=NULL right now (i.e. the injected context would go empty) —
#      the opposite of the shared-across-agents goal. Force it to None so
#      automatic recall always stays shared; explicit self-audit can still
#      pass author_id via a direct recall() tool call.
# Verified against mnemosyne-memory==3.5.0 (hermes_memory_provider bundled by
# it); re-check both anchors on upgrade.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import importlib.util as u
f = u.find_spec("hermes_memory_provider").origin
s = open(f).read()
marker = "# hermes-patch: author attribution"
if marker in s:
    print("author-attribution patch already present:", f)
else:
    old1 = ("                BeamMemory = _get_beam_class()\n"
            "                self._beam = BeamMemory(session_id=self._session_id)\n"
            "                logger.info(\"Mnemosyne initialized: session=%s\", self._session_id)\n")
    new1 = ("                BeamMemory = _get_beam_class()\n"
            "                " + marker + "\n"
            "                self._beam = BeamMemory(\n"
            "                    session_id=self._session_id,\n"
            "                    author_id=os.environ.get(\"MNEMOSYNE_AUTHOR_ID\"),\n"
            "                    author_type=os.environ.get(\"MNEMOSYNE_AUTHOR_TYPE\"),\n"
            "                )\n"
            "                logger.info(\"Mnemosyne initialized: session=%s, author=%s\", self._session_id, self._beam.author_id)\n")
    old2 = ("            author_id = self._beam.author_id or os.environ.get(\"MNEMOSYNE_AUTHOR_ID\")\n")
    new2 = ("            author_id = None  " + marker + " -- automatic recall must stay shared; see Dockerfile\n")
    if s.count(old1) == 1 and s.count(old2) == 1:
        open(f, "w").write(s.replace(old1, new1).replace(old2, new2))
        print("patched: author attribution wired (write-stamp + shared-recall guard):", f)
    else:
        print("WARNING: author-attribution anchors not found (%d/%d); skipping." % (s.count(old1), s.count(old2)))
PY

# # Persist the mnemosyne embedding-model cache for root-context CLI runs.
# # mnemosyne hardcodes the fastembed cache to ~/.hermes/cache/fastembed
# # (embeddings.py; no env override — MNEMOSYNE_DATA_DIR only moves the DB).
# # The gateway and the PATH shim (/opt/hermes/bin/hermes) run with
# # HOME=/opt/data, so their cache lands on the volume; but invoking the venv
# # binary directly as root (`docker exec … /opt/hermes/.venv/bin/hermes`, as
# # older upstream docs suggest) gets HOME=/root and re-downloads the ~65MB
# # model into the ephemeral layer after every redeploy. Symlink root's cache
# # dir onto the volume so every context shares the persistent copy (dangles
# # at build time; resolves once the volume is mounted — mnemosyne's makedirs
# # follows it). The rm also drops the stray ~/.hermes/mnemosyne DB the patch
# # step above bakes into the image (importing mnemosyne as root initializes
# # a DB at build time).
# RUN rm -rf /root/.hermes && mkdir -p /root/.hermes && \
#     ln -s /opt/data/.hermes/cache /root/.hermes/cache

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
        > /etc/profile.d/99-hermes-path.sh
#     printf '%s\n' \
#         '#!/bin/sh' \
#         '# docker-exec privilege-drop shim for the mnemosyne CLI; mirrors' \
#         '# /opt/hermes/bin/hermes (see hermes-exec-shim.sh for rationale).' \
#         'REAL=/opt/hermes/.venv/bin/mnemosyne' \
#         '[ -x "$REAL" ] || { echo "mnemosyne-shim: $REAL missing" >&2; exit 127; }' \
#         'if [ "$(id -u)" != "0" ]; then exec "$REAL" "$@"; fi' \
#         'export HOME=/opt/data' \
#         'exec /command/s6-setuidgid hermes "$REAL" "$@"' \
#         > /opt/hermes/bin/mnemosyne && \
#     chmod +x /opt/hermes/bin/mnemosyne

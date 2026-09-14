FROM nousresearch/hermes-agent:latest

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends syncthing && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code byterover-cli ntn @upstash/cli vercel @posthog/cli supabase && npm cache clean --force && \
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
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "mnemosyne-memory==3.15.1" sqlite-vec==0.1.9 && \
    PKGDIR="$(/opt/hermes/.venv/bin/python -c 'import importlib.util as u; print(u.find_spec("hermes_memory_provider").submodule_search_locations[0])')" && \
    printf '%s\n' \
        'name: mnemosyne' \
        'version: 3.15.1' \
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
# Re-verified at mnemosyne-memory==3.15.1 (bumped 2026-08-22): still unfixed
# upstream, both anchors still match (count=1 each) despite ~220 commits of
# drift since 3.5.0. register_hermes_host_llm import confirmed still valid.
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
# The regex layer has NO config gate (ignore_patterns/sync_roles/the prompt don't
# reach it — confirmed against source + docs); even upstream main only hides its
# output at recall, still writing the junk. Neutralize it by overriding the method with
# a no-op (returns the empty per-type counts dict its callers expect). The LLM
# extractor + preferences are unaffected. Idempotent; drop this once on a version that
# gates the extractor at write time.
# Re-verified at 3.15.1 (2026-08-22): the regex patterns got more refined (added
# transient-keyword filtering etc.) but the method is still unconditional with no
# gate — patch still applies cleanly and is still needed. Append-based, so it's
# version-agnostic by design (doesn't depend on the method body matching anything).
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
# Re-verified at mnemosyne-memory==3.15.1 (2026-08-22, bundled hermes_memory_provider
# copy — the actively-maintained sibling copy at integrations/hermes/ already has a
# fixed shared_surface_path default, but this bundled copy still has the same bad
# site-packages-relative default from 3.5.0; the config-level workaround in
# docker-compose.yaml/config.yaml remains necessary regardless). Both anchors here
# still match unchanged despite ~220 commits of drift since 3.5.0.
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

# Fix low-precision canonical model-slot injection in the mnemosyne Hermes
# provider. `_prefetch_model_slots` runs on EVERY non-trivial user turn
# (hermes agent/turn_context.py -> _memory_turn_start_and_prefetch) and injects
# up to 3 canonical "model slots" into the prompt. Upstream ranks candidates by
# RAW TOKEN OVERLAP COUNT, sorts by (overlap, confidence), and ALWAYS fills to
# the cap -- there is no relevance floor and no empty-result path. Measured
# against the live store (22 model slots): the query "what tools and workflow
# style does the user prefer" injected an unrelated ENT/sinus medical fact,
# because the two shared exactly ['does', 'user'] -- both function words.
#
# Three fixes were measured out-of-process against a read-only snapshot before
# landing this; the first two FAILED and are recorded so nobody retries them:
#   1. MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_OVERLAP=2 -- does NOT help. The junk
#      match is itself 2 tokens ('does'+'user'), so it survives, while genuine
#      single-specific-token matches are pruned: career 3->0, dinefile 3->0.
#      Strictly worse. Do not set this.
#   2. Stopwords alone -- evicts the ENT slot but backfills the freed slots with
#      equally irrelevant ones (always-fill), and drops a good hit. Net neutral.
#   3. IDF alone -- also fails: IDF measures corpus RARITY, not meaninglessness.
#      Across 22 slots 'does' has df=2 (idf 2.04) while 'user' has df=5
#      (idf 1.34), so IDF actively REWARDS the rare function word.
# What works is all of it together, which this patch installs:
#   * stopword pass (kills rare-but-meaningless function words like 'does')
#   * IDF weighting (kills corpus-common tokens like 'workflow', df=9)
#   * name/category boost (a slot whose NAME carries the term beats one that
#     merely mentions it in prose -- restores correct ordering on career)
#   * a relevance floor, and permission to return "" when nothing clears it
# Cap stays at 3 (MNEMOSYNE_PREFETCH_MODEL_SLOT_LIMIT still honored), explicit
# canonical recall/writes are untouched, author + shared-recall behavior is
# untouched, and autosave scope behavior is untouched.
#
# Installed by REBINDING the method (like the beam regex-extractor patch above)
# rather than by string surgery, so there is no fragile source anchor to rot.
#
# FAIL-CLOSED, unlike the older patches above. Those warn-and-continue, which
# means a silent revert to upstream behavior if an anchor ever moves. This one
# must not do that: a silent revert here restores the defective ranking on every
# turn with no signal. So:
#   * the apply step exits non-zero if required module globals are missing, if
#     the appended source does not compile, or if the write fails;
#   * the appended runtime shim RAISES instead of swallowing, so a broken
#     install surfaces as an import error rather than as silent degradation;
#   * a separate behavioral self-test RUN below fails the BUILD unless the
#     patched ranking actually demonstrates the required properties.
# The marker check stays idempotent (re-running is a no-op, not a failure).
RUN /opt/hermes/.venv/bin/python - <<'PY'
import importlib.util as u, sys
spec = u.find_spec("hermes_memory_provider")
if spec is None or not spec.origin:
    sys.exit("FATAL: hermes_memory_provider not importable; cannot install model-slot patch")
f = spec.origin
s = open(f).read()
marker = "# hermes-patch: model-slot idf ranking"
if marker in s:
    print("model-slot idf patch already present (idempotent no-op):", f)
    sys.exit(0)
# The shim closes over these module globals; if upstream renames any of them the
# rebind would raise at import time. Fail the BUILD here instead.
required = [
    "_PREFETCH_MODEL_SLOT_STOPWORDS", "_prefetch_tokens", "logger",
    "_prefetch_content_char_limit", "_format_prefetch_content",
    "MnemosyneMemoryProvider",
]
missing = [r for r in required if r not in s]
if missing:
    sys.exit("FATAL: model-slot patch prerequisites missing from %s: %s" % (f, missing))
addition = '''

''' + marker + '''  -- see Dockerfile for rationale and rejected alternatives.
def _hermes_patch_model_slot_ranking():
    import math as _math, os as _os, re as _re
    _EXTRA_STOP = frozenset({
        "does", "did", "done", "doing", "user", "users", "their", "theirs",
        "using", "use", "used", "uses", "each", "every", "make", "makes", "made",
        "get", "gets", "got", "can", "will", "may", "must", "any", "all", "some",
        "own", "new", "old", "one", "two", "via", "per", "such", "same", "other",
    })
    _stop = frozenset(_PREFETCH_MODEL_SLOT_STOPWORDS) | _EXTRA_STOP

    def _toks(content):
        out = set()
        for token in _prefetch_tokens(content):
            if token in _stop:
                continue
            out.add(token)
            for part in _re.split(r"[_:/.-]+", token):
                if len(part) > 2 and part not in _stop:
                    out.add(part)
        return out

    def _num(name, default, cast):
        try:
            return cast(_os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return cast(default)

    def _prefetch_model_slots(self, query, profile):
        beam = self._beam
        if beam is None:
            return ""
        qt = _toks(query)
        if not qt:
            return ""
        max_slots = _num("MNEMOSYNE_PREFETCH_MODEL_SLOT_LIMIT", 3, int)
        min_overlap = _num("MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_OVERLAP", 1, int)
        floor = _num("MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_IDF", 2.0, float)
        boost = _num("MNEMOSYNE_PREFETCH_MODEL_SLOT_NAME_BOOST", 2.0, float)
        try:
            store = getattr(beam, "canonical", None)
            if store is None:
                from mnemosyne.core.canonical import CanonicalStore
                store = CanonicalStore(db_path=beam.db_path, conn=beam.conn)
                beam.canonical = store
            owner_id = self._canonical_owner()
            rows = []
            for category in ("model:user", "model:workflow", "model:project", "model:agent"):
                rows.extend(store.list(owner_id, category=category))
        except Exception as e:
            logger.debug("Mnemosyne model-slot prefetch failed (non-fatal): %s", e)
            return ""
        if not rows:
            return ""
        toksets, nametoks, df = [], [], {}
        for row in rows:
            t = _toks(" ".join(str(row.get(k) or "") for k in ("category", "name", "body")))
            nt = _toks(" ".join(str(row.get(k) or "") for k in ("category", "name")))
            toksets.append(t)
            nametoks.append(nt)
            for tok in t:
                df[tok] = df.get(tok, 0) + 1
        n = len(rows)
        scored = []
        for row, t, nt in zip(rows, toksets, nametoks):
            shared = qt & t
            if len(shared) < min_overlap:
                continue
            weight = 0.0
            for tok in shared:
                idf = _math.log((n + 1.0) / (df.get(tok, 0) + 1.0))
                weight += idf * (boost if tok in nt else 1.0)
            if weight < floor:
                continue
            scored.append((weight, float(row.get("confidence") or 0.0), row))
        if not scored:
            return ""
        scored.sort(key=lambda i: (i[0], i[1]), reverse=True)
        content_limit = _prefetch_content_char_limit() or profile.content_char_limit
        lines = ["## Mnemosyne Model Context"]
        for _, _, row in scored[:max_slots]:
            body = _format_prefetch_content(str(row.get("body") or ""), content_limit)
            body = " ".join(body.split())
            if not body:
                continue
            category = str(row.get("category") or "model")
            name = str(row.get("name") or "slot").replace("_", " ")
            lines.append("  [%s] %s: %s" % (category, name, body))
        return "\\n".join(lines) if len(lines) > 1 else ""

    MnemosyneMemoryProvider._prefetch_model_slots = _prefetch_model_slots
    _prefetch_model_slots.__hermes_idf_ranked__ = True


# Fail loudly rather than silently reverting to upstream token-overlap ranking.
_hermes_patch_model_slot_ranking()
'''
try:
    compile(s + addition, f, "exec")
except SyntaxError as e:
    sys.exit("FATAL: model-slot patch would produce invalid source: %s" % e)
with open(f, "a") as fh:
    fh.write(addition)
print("patched: model-slot ranking now stopword+IDF+name-boost with relevance floor:", f)
PY

# Behavioral self-test for the model-slot patch. FAILS THE BUILD if the patched
# ranking does not actually hold the properties it exists to provide. Without
# this the image could ship a marker-present-but-ineffective patch. Uses
# synthetic canonical slots in a throwaway DB; never reads the real store.
RUN /opt/hermes/.venv/bin/python - <<'PY'
import os, sys, tempfile, shutil
TMP = tempfile.mkdtemp(prefix="mslot-buildtest-")
os.environ["MNEMOSYNE_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["MNEMOSYNE_DATA_DIR"], exist_ok=True)
for k in ("MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_OVERLAP",
          "MNEMOSYNE_PREFETCH_MODEL_SLOT_MIN_IDF",
          "MNEMOSYNE_PREFETCH_MODEL_SLOT_NAME_BOOST"):
    os.environ.pop(k, None)
os.environ["MNEMOSYNE_PREFETCH_MODEL_SLOT_LIMIT"] = "3"
import hermes_memory_provider as H

fn = H.MnemosyneMemoryProvider._prefetch_model_slots
if not getattr(fn, "__hermes_idf_ranked__", False):
    sys.exit("FATAL: _prefetch_model_slots is NOT the patched implementation")

FIX = [
    ("model:project", "career_hub_migration", "Migrate the career hub into GBrain with event timestamps."),
    ("model:project", "career_pilot_environment", "The career pilot environment variables are configured."),
    ("model:workflow", "career_temporal_normalization", "Normalize career hub content preserving dates."),
    ("model:project", "dinefile_repository", "DineFile repository holds the recommendation extractor."),
    ("model:workflow", "dinefile_skill_policy", "DineFile marketing launch plans and skill policy."),
    ("model:project", "deployment_topology", "Coolify deployment topology for the server services."),
    ("model:project", "ent_sinus_decision", "The user is evaluating whether treatment does improve breathing."),
    ("model:workflow", "tv_mode_lighting", "When TV mode is activated turn off all lights except the lamp."),
    ("model:workflow", "tv_light_combo", "Turn on the TV lights while keeping the bedroom lights on."),
    ("model:user", "command_format", "Prefers copy-pastable shell commands."),
    ("model:user", "anonymity", "Wants to stay anonymous when launching apps."),
    ("model:agent", "discord_admin", "May administer Discord channels."),
]

def names(block):
    out = []
    for line in (block.split("\n")[1:] if block else []):
        t = " ".join(line.split())
        if not t:
            continue
        nm = t.split("]", 1)[1].split(":", 1)[0].strip() if "]" in t else t
        out.append(nm.replace(" ", "_"))
    return out

def run(session, isolated):
    p = H.MnemosyneMemoryProvider()
    if isolated:
        p._profile_isolation_enabled = True
    p.initialize(session_id=session)
    beam = p._beam
    store = getattr(beam, "canonical", None)
    if store is None:
        from mnemosyne.core.canonical import CanonicalStore
        store = CanonicalStore(db_path=beam.db_path, conn=beam.conn)
        beam.canonical = store
    owner = p._canonical_owner()
    for c, nm, b in FIX:
        store.remember(owner, c, nm, b, source="buildtest", confidence=0.9)
    prof = H._resolve_profile("general")
    return p, store, owner, {
        "generic":  names(p._prefetch_model_slots("what tools and workflow style does the user prefer", prof)),
        "career":   names(p._prefetch_model_slots("career goals job role work experience", prof)),
        "dinefile": names(p._prefetch_model_slots("dinefile marketing and launch plans", prof)),
        "project":  names(p._prefetch_model_slots("coolify deployment server topology", prof)),
        "home":     names(p._prefetch_model_slots("turn on tv mode lights living room", prof)),
        "nomatch":  names(p._prefetch_model_slots("medieval falconry equipment maintenance schedule", prof)),
    }

fails = []
def need(cond, label):
    print("  %-4s %s" % ("ok" if cond else "FAIL", label))
    if not cond:
        fails.append(label)

for isolated in (False, True):
    tag = "profile-isolated" if isolated else "normal"
    p, store, owner, g = run("buildtest-%s" % tag, isolated)
    print("[%s] %s" % (tag, {k: len(v) for k, v in g.items()}))
    need("ent_sinus_decision" not in g["generic"], "[%s] generic words cannot select unrelated slot" % tag)
    need(g["nomatch"] == [], "[%s] no-match returns an EMPTY block" % tag)
    need(any("career" in x for x in g["career"]), "[%s] career query keeps a career slot" % tag)
    need(any("dinefile" in x for x in g["dinefile"]), "[%s] dinefile query keeps a dinefile slot" % tag)
    need(any("deployment" in x for x in g["project"]), "[%s] project query keeps a deployment slot" % tag)
    need(all(len(v) <= 3 for v in g.values()), "[%s] slot cap remains 3" % tag)
    need(len([x for x in g["home"] if x.startswith("tv_")]) <= 2, "[%s] contradictory slots not worsened" % tag)
    # Explicit canonical recall/writes must be unaffected by the ranking change.
    row = store.recall(owner, "model:project", "ent_sinus_decision")
    need(row is not None and "breathing" in (row.get("body") or ""),
         "[%s] explicit canonical recall unchanged" % tag)
    need(len(store.list(owner, category="model:project")) >= 4,
         "[%s] explicit canonical list/writes unchanged" % tag)

shutil.rmtree(TMP, ignore_errors=True)
if fails:
    sys.exit("FATAL: model-slot patch self-test failed: %s" % fails)
print("model-slot patch behavioral self-test PASSED (normal + profile-isolated)")
PY

# ---------------------------------------------------------------------------
# CONSOLIDATED PATCH GATE. Fails the build unless every compatibility patch is
# actually in effect.
#
# WHY THIS EXISTS. Patches 1-4 are anchor-based and warn-rather-than-fail: if
# upstream renames or reformats the code they target, they skip and the build
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
import importlib.util as u, os, re, sys, tempfile, shutil

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

# --- patch 2: host LLM must be re-registerable for extraction --------------
# Invariant: the adapter entrypoint the shim calls still exists AND local_llm
# reaches it from the "backend is None" path.
try:
    from hermes_memory_provider.hermes_llm_adapter import register_hermes_host_llm  # noqa
    entry_ok = callable(register_hermes_host_llm)
except Exception as e:
    entry_ok = False
    print("     import error:", e)
check("patch2 register_hermes_host_llm importable+callable", entry_ok)
import mnemosyne.core.local_llm as _ll
_ll_src = open(_ll.__file__).read()
check("patch2 lazy re-register wired into local_llm",
      _ll_src.count("register_hermes_host_llm") >= 2,
      "%d refs" % _ll_src.count("register_hermes_host_llm"))

# --- patch 3: regex fact extractor must be inert ---------------------------
# Functional: call it and require an empty result.
import mnemosyne.core.beam as _beam
try:
    inert = _beam.BeamMemory.extract_and_store_facts(object()) == {}
except Exception as e:
    inert = False
    print("     call raised:", e)
check("patch3 regex extractor inert (functional call)", inert)

# --- patch 4: author attribution -------------------------------------------
# Functional: a write must be stamped with MNEMOSYNE_AUTHOR_ID, and automatic
# recall must NOT be author-filtered (or shared rows go invisible).
TMP = tempfile.mkdtemp(prefix="patchgate-")
os.environ["MNEMOSYNE_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["MNEMOSYNE_DATA_DIR"], exist_ok=True)
os.environ["MNEMOSYNE_AUTHOR_ID"] = "gatecheck"
os.environ["MNEMOSYNE_AUTHOR_TYPE"] = "agent"
stamped = shared = False
try:
    import hermes_memory_provider as H
    prov = H.MnemosyneMemoryProvider()
    prov.initialize(session_id="patch-gate")
    prov._beam.remember(content="patch gate probe memory", source="gatecheck",
                        importance=0.9, scope="global")
    import sqlite3
    conn = sqlite3.connect(prov._beam.db_path)
    row = conn.execute("SELECT author_id FROM working_memory WHERE source=?",
                       ("gatecheck",)).fetchone()
    conn.close()
    stamped = bool(row) and row[0] == "gatecheck"
    src4 = open(u.find_spec("hermes_memory_provider").origin).read()
    m = re.search(r"def _prefetch_bank\(.*?\n(.*?)\n    def ", src4, re.S)
    body = m.group(1) if m else ""
    shared = bool(re.search(r"author_id\s*=\s*None", body))
except Exception as e:
    print("     patch4 probe error:", e)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
check("patch4 writes are author-stamped (functional)", stamped)
check("patch4 automatic recall not author-filtered", shared)

# --- patches 5b / 6 already self-gate, re-assert cheaply -------------------
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

fn6 = H.MnemosyneMemoryProvider._prefetch_model_slots
check("patch6 model-slot ranking is the patched impl",
      getattr(fn6, "__hermes_idf_ranked__", False))

print("")
if failures:
    sys.exit("FATAL: compatibility patch gate failed: %s" % failures)
print("compatibility patch gate PASSED -- all patches verified in effect")
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

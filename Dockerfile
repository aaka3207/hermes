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
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "mnemosyne-memory[embeddings]==3.0.0" sqlite-vec==0.1.9 && \
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

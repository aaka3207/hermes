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

FROM nousresearch/hermes-agent:latest

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends syncthing && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code && npm cache clean --force && \
    cd /opt/hermes && uv sync --frozen --no-install-project --extra all --extra hindsight && \
    chown -R hermes:hermes /opt/hermes/.venv

FROM nousresearch/hermes-agent:latest

USER root
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
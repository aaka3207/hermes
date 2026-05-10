# Hermes Agent Dockerfile for Coolify
FROM nikolaik/python-nodejs:python3.11-nodejs20

# Install dependencies needed for Hermes and MCP
RUN pip install mcp honcho-ai-sdk

# Create a non-root user
RUN useradd -ms /bin/bash hermes
USER hermes
WORKDIR /home/hermes

# Create directory for persistent hermes config
RUN mkdir -p /home/hermes/.hermes

# Start hermes with discord enabled
# We use --port 8080 as Coolify defaults to this for internal health checks
CMD ["hermes", "gateway", "run", "--platform", "discord", "--port", "8080"]

# Hermes Agent Dockerfile for Coolify
FROM nikolaik/python-nodejs:python3.11-nodejs20

# Install Hermes Agent and core SDKs
RUN pip install hermes-agent mcp honcho-ai-sdk

# Create a non-root user
RUN useradd -ms /bin/bash hermes
USER hermes
WORKDIR /home/hermes

# Create directory for persistent hermes config
RUN mkdir -p /home/hermes/.hermes

# Set the official HERMES_HOME to match the volume mount
ENV HERMES_HOME=/opt/data

# Start hermes with discord enabled
CMD ["hermes", "gateway", "run", "--platform", "discord", "--port", "8642"]

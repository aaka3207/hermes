# Hermes Agent Dockerfile for Coolify
FROM nikolaik/python-nodejs:python3.11-nodejs20

# Install dependencies
RUN pip install mcp honcho-ai-sdk

# Create a non-root user
RUN useradd -ms /bin/bash hermes
USER hermes
WORKDIR /home/hermes

# Create directory for persistent hermes config
RUN mkdir -p /home/hermes/.hermes

# Hermes runs on 8080 by default for the gateway
EXPOSE 8080

# Start hermes with discord enabled
CMD ["hermes", "gateway", "--platform", "discord"]

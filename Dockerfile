FROM python:3.11-slim

ARG CLAUDE_CODE_NPM_SPEC=@anthropic-ai/claude-code

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTDRAW_WORKSPACE_ROOT=/workspaces \
    FRONTDRAW_HOST=0.0.0.0 \
    FRONTDRAW_PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    graphviz \
    bash \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.118.2 \
    uvicorn==0.37.0

RUN useradd -m -u 1000 -s /bin/bash sandbox

RUN npm install -g "${CLAUDE_CODE_NPM_SPEC}" \
    && claude --version

COPY . /app

RUN mkdir -p /workspaces

EXPOSE 8000

CMD ["sh", "-c", "if [ -f /app/server.py ]; then exec python3 /app/server.py; else exec python3 /app/harbor/frontdraw_http/server.py; fi"]

FROM python:3.11-slim

ARG CLAUDE_CODE_NPM_SPEC=@anthropic-ai/claude-code

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PUPPETEER_SKIP_DOWNLOAD=1 \
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
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-unifont \
    librsvg2-bin \
    poppler-utils \
    ghostscript \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-pictures \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.118.2 \
    uvicorn==0.37.0 \
    playwright==1.53.0

RUN python -m playwright install chromium

RUN useradd -m -u 1000 -s /bin/bash sandbox

RUN npm install -g @mermaid-js/mermaid-cli

RUN npm install -g "${CLAUDE_CODE_NPM_SPEC}" \
    && claude --version

COPY . /app

RUN mkdir -p /workspaces

EXPOSE 8000

CMD ["sh", "-c", "if [ -f /app/server.py ]; then exec python3 /app/server.py; else exec python3 /app/harbor/frontdraw_http/server.py; fi"]

# Production image for mcp-ops-server: the MCP server only (no evals, no tests).
# Base pinned by digest so a rebuild gets the same OS and Python; bump it deliberately.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY mcp_ops_server ./mcp_ops_server
RUN pip install . && rm -rf /app/mcp_ops_server /app/pyproject.toml

# Unprivileged user; /data holds the tokens file (mounted at run time, never baked in).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app /data
USER app

# Listen on all interfaces inside the container. Every /mcp request needs a bearer token.
ENV MCP_OPS_HOST=0.0.0.0 \
    MCP_OPS_PORT=8000 \
    MCP_OPS_TOKENS_FILE=/data/tokens.json
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["python", "-m", "mcp_ops_server"]

FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 mcp \
    && useradd --system --uid 10001 --gid mcp --home-dir /app --shell /usr/sbin/nologin mcp \
    && mkdir -p /app /data \
    && chown -R mcp:mcp /app /data

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

USER 10001:10001

EXPOSE 8787

ENTRYPOINT ["rancher-rke2-mcp"]

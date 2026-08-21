# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-fast

COPY . .

RUN uv sync --locked --no-dev --package worker-fast --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/worker-fast/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    WORKER_ENVIRONMENT=production

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-fast \
    && useradd --uid 10001 --gid worker-fast --no-create-home \
        --shell /usr/sbin/nologin worker-fast

WORKDIR /opt/worker-fast

COPY --from=builder /opt/worker-fast/.venv /opt/worker-fast/.venv

USER 10001:10001

ENTRYPOINT ["/opt/worker-fast/.venv/bin/worker-fast"]

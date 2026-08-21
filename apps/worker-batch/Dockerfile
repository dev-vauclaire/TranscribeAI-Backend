# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-batch

COPY . .

RUN uv sync --locked --no-dev --package worker-batch --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/worker-batch/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    WORKER_ENVIRONMENT=production

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-batch \
    && useradd --uid 10001 --gid worker-batch --no-create-home \
        --shell /usr/sbin/nologin worker-batch

WORKDIR /opt/worker-batch

COPY --from=builder /opt/worker-batch/.venv /opt/worker-batch/.venv

USER 10001:10001

ENTRYPOINT ["/opt/worker-batch/.venv/bin/worker-batch"]

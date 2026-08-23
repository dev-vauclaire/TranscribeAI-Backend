# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/dispatcher

COPY . .

RUN uv sync --locked --no-dev --package dispatcher --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/dispatcher/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DISPATCHER_BATCH_SIZE=100 \
    RECONCILIATION_TIMEOUT_SECONDS=300 \
    MAX_ATTEMPTS=3

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dispatcher \
    && useradd --uid 10001 --gid dispatcher --no-create-home \
        --shell /usr/sbin/nologin dispatcher

WORKDIR /opt/dispatcher

COPY --from=builder /opt/dispatcher/.venv /opt/dispatcher/.venv

USER 10001:10001

ENTRYPOINT ["/opt/dispatcher/.venv/bin/dispatch-transcriptions"]

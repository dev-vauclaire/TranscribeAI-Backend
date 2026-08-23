# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/migration

COPY . .

RUN uv sync --locked --no-dev --package migration --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/migration/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 migration \
    && useradd --uid 10001 --gid migration --no-create-home \
        --shell /usr/sbin/nologin migration

WORKDIR /opt/migration

COPY --from=builder /opt/migration/.venv /opt/migration/.venv

USER 10001:10001

ENTRYPOINT ["/opt/migration/.venv/bin/database-migrate"]

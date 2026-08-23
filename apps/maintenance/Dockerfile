# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/maintenance

COPY . .

RUN uv sync --locked --no-dev --package maintenance --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/maintenance/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AUDIO_STORAGE_PATH=/data/transcriptions

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 maintenance \
    && useradd --uid 10001 --gid maintenance --no-create-home \
        --shell /usr/sbin/nologin maintenance \
    && mkdir --parents /data/transcriptions \
    && chown maintenance:maintenance /data/transcriptions

WORKDIR /opt/maintenance

COPY --from=builder /opt/maintenance/.venv /opt/maintenance/.venv

USER 10001:10001

ENTRYPOINT ["/opt/maintenance/.venv/bin/cleanup-storage"]

# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/api

COPY . .

RUN uv sync --locked --no-dev --package api --no-editable


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/api/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    API_MAX_UPLOAD_SIZE_BYTES=104857600 \
    AUDIO_STORAGE_PATH=/data/transcriptions \
    FFPROBE_PATH=ffprobe \
    FFPROBE_TIMEOUT_SECONDS=30 \
    API_FAST_MAX_DURATION_SECONDS=900 \
    API_BATCH_MAX_DURATION_SECONDS=14400

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        'ffmpeg=7:7.*' \
        'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system api \
    && useradd --system --gid api --no-create-home api \
    && mkdir --parents /data/transcriptions \
    && chown api:api /data/transcriptions

WORKDIR /opt/api

COPY --from=builder /opt/api/.venv /opt/api/.venv

USER api

EXPOSE 8000

ENTRYPOINT ["/opt/api/.venv/bin/api"]

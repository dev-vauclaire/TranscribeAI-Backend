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
    API_READINESS_TIMEOUT_SECONDS=2 \
    API_FAST_MAX_DURATION_SECONDS=900 \
    API_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS=14400

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        'ffmpeg=7:7.*' \
        'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 api \
    && useradd --uid 10001 --gid api --no-create-home \
        --shell /usr/sbin/nologin api \
    && mkdir --parents /data/transcriptions \
    && chown api:api /data/transcriptions

WORKDIR /opt/api

COPY --from=builder /opt/api/.venv /opt/api/.venv

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).read()"]

ENTRYPOINT ["/opt/api/.venv/bin/api"]

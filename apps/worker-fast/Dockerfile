# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-fast

COPY . .

RUN uv sync --locked --no-dev --package worker-fast --no-editable --extra gpu


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/worker-fast/.venv/bin:$PATH" \
    LD_LIBRARY_PATH="/opt/worker-fast/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/worker-fast/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    AUDIO_STORAGE_PATH=/data/transcriptions \
    WORKER_ENVIRONMENT=production \
    WORKER_TRANSCRIBER_BACKEND=faster-whisper \
    WORKER_TRANSCRIBER_MODEL=large-v3-turbo \
    WORKER_TRANSCRIBER_DEVICE=cuda \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=float16

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-fast \
    && useradd --uid 10001 --gid worker-fast --no-create-home \
        --shell /usr/sbin/nologin worker-fast \
    && mkdir --parents /data/transcriptions /models/huggingface \
    && chown --recursive worker-fast:worker-fast /models

WORKDIR /opt/worker-fast

COPY --from=builder /opt/worker-fast/.venv /opt/worker-fast/.venv

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/opt/worker-fast/.venv/bin/worker-healthcheck"]

ENTRYPOINT ["/opt/worker-fast/.venv/bin/worker-fast"]

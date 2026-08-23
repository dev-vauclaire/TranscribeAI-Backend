# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder-base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-fast

COPY . .


FROM builder-base AS builder-fake

RUN uv sync --locked --no-dev --package worker-fast --no-editable


FROM builder-base AS builder-cpu

RUN uv sync --locked --no-dev --package worker-fast --no-editable --extra cpu


FROM builder-base AS builder-gpu

RUN uv sync --locked --no-dev --package worker-fast --no-editable --extra gpu


FROM python:3.12-slim-trixie AS runtime-base

ENV PATH="/opt/worker-fast/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AUDIO_STORAGE_PATH=/data/transcriptions

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-fast \
    && useradd --uid 10001 --gid worker-fast --create-home \
        --shell /usr/sbin/nologin worker-fast \
    && mkdir --parents /data/transcriptions \
    && chown worker-fast:worker-fast /data/transcriptions

WORKDIR /opt/worker-fast

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["/opt/worker-fast/.venv/bin/worker-healthcheck"]

ENTRYPOINT ["/opt/worker-fast/.venv/bin/worker-fast"]


FROM runtime-base AS runtime-fake

ENV WORKER_ENVIRONMENT=development \
    WORKER_TRANSCRIBER_BACKEND=fake \
    WORKER_TRANSCRIBER_DEVICE=cpu \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=int8

COPY --from=builder-fake /opt/worker-fast/.venv /opt/worker-fast/.venv


FROM runtime-base AS runtime-cpu

ENV HF_HOME=/home/worker-fast/.cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    WORKER_ENVIRONMENT=production \
    WORKER_TRANSCRIBER_BACKEND=faster-whisper \
    WORKER_TRANSCRIBER_MODEL=large-v3-turbo \
    WORKER_TRANSCRIBER_DEVICE=cpu \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=int8

RUN mkdir --parents /home/worker-fast/.cache/huggingface

COPY --from=builder-cpu /opt/worker-fast/.venv /opt/worker-fast/.venv


FROM runtime-base AS runtime-gpu

ENV LD_LIBRARY_PATH="/opt/worker-fast/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/worker-fast/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib" \
    HF_HOME=/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    WORKER_ENVIRONMENT=production \
    WORKER_TRANSCRIBER_BACKEND=faster-whisper \
    WORKER_TRANSCRIBER_MODEL=large-v3-turbo \
    WORKER_TRANSCRIBER_DEVICE=cuda \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=float16

USER 0

RUN mkdir --parents /models/huggingface \
    && chown --recursive worker-fast:worker-fast /models

COPY --from=builder-gpu /opt/worker-fast/.venv /opt/worker-fast/.venv

USER 10001:10001

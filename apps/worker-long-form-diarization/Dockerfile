# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder-base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-long-form-diarization

COPY . .


FROM builder-base AS builder-fake

RUN uv sync --locked --no-dev --package worker-long-form-diarization --no-editable


FROM builder-base AS builder-cpu

RUN uv sync --locked --no-dev --package worker-long-form-diarization \
    --no-editable --extra cpu


FROM builder-base AS builder-gpu

RUN uv sync --locked --no-dev --package worker-long-form-diarization \
    --no-editable --extra gpu


FROM python:3.12-slim-trixie AS runtime-base

ENV PATH="/opt/worker-long-form-diarization/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/worker-long-form-diarization \
    AUDIO_STORAGE_PATH=/data/transcriptions

RUN apt-get update \
    && apt-get install --yes --no-install-recommends 'libpq5=17.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-long-form-diarization \
    && useradd --uid 10001 --gid worker-long-form-diarization --create-home \
        --shell /usr/sbin/nologin worker-long-form-diarization \
    && mkdir --parents /data/transcriptions \
    && chown worker-long-form-diarization:worker-long-form-diarization \
        /data/transcriptions

WORKDIR /opt/worker-long-form-diarization

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["/opt/worker-long-form-diarization/.venv/bin/worker-healthcheck"]

ENTRYPOINT ["/opt/worker-long-form-diarization/.venv/bin/worker-long-form-diarization"]


FROM runtime-base AS runtime-fake

ENV WORKER_ENVIRONMENT=development \
    WORKER_TRANSCRIBER_BACKEND=fake \
    WORKER_TRANSCRIBER_DEVICE=cpu \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=default \
    WORKER_TRANSCRIBER_MODEL_DIRECTORY=/home/worker-long-form-diarization/models

COPY --from=builder-fake /opt/worker-long-form-diarization/.venv \
    /opt/worker-long-form-diarization/.venv


FROM runtime-base AS runtime-ml-base

ENV CUDA_CACHE_PATH=/models/cache/cuda \
    HF_HOME=/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    MPLCONFIGDIR=/models/cache/matplotlib \
    TORCH_HOME=/models/torch \
    TORCHINDUCTOR_CACHE_DIR=/models/cache/torchinductor \
    XDG_CACHE_HOME=/models/cache \
    XDG_CONFIG_HOME=/models/config \
    NLTK_DATA=/models/nltk \
    WORKER_ENVIRONMENT=production \
    WORKER_TRANSCRIBER_BACKEND=whisperx \
    WORKER_TRANSCRIBER_MODEL=large-v3-turbo \
    WORKER_TRANSCRIBER_MODEL_DIRECTORY=/models

USER 0

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        'ffmpeg=7:*' \
        'libgomp1=14.*' \
        'libsndfile1=1.2.*' \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir --parents \
        /models/alignment \
        /models/cache \
        /models/config \
        /models/huggingface \
        /models/nltk \
        /models/pyannote \
        /models/torch \
        /models/whisperx \
    && chown --recursive worker-long-form-diarization:worker-long-form-diarization \
        /models

USER 10001:10001


FROM runtime-ml-base AS runtime-cpu

ENV WORKER_TRANSCRIBER_DEVICE=cpu \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=int8

COPY --from=builder-cpu /opt/worker-long-form-diarization/.venv \
    /opt/worker-long-form-diarization/.venv

USER 10001:10001


FROM runtime-ml-base AS runtime-gpu

ENV LD_LIBRARY_PATH="/opt/worker-long-form-diarization/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/worker-long-form-diarization/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib" \
    WORKER_TRANSCRIBER_DEVICE=cuda \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=default

COPY --from=builder-gpu /opt/worker-long-form-diarization/.venv \
    /opt/worker-long-form-diarization/.venv

USER 10001:10001

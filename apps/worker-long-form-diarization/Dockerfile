# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/worker-long-form-diarization

COPY . .

RUN uv sync --locked --no-dev --package worker-long-form-diarization \
    --no-editable --extra gpu


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/opt/worker-long-form-diarization/.venv/bin:$PATH" \
    LD_LIBRARY_PATH="/opt/worker-long-form-diarization/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/worker-long-form-diarization/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/worker-long-form-diarization \
    CUDA_CACHE_PATH=/models/cache/cuda \
    HF_HOME=/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    MPLCONFIGDIR=/models/cache/matplotlib \
    TORCH_HOME=/models/torch \
    TORCHINDUCTOR_CACHE_DIR=/models/cache/torchinductor \
    XDG_CACHE_HOME=/models/cache \
    XDG_CONFIG_HOME=/models/config \
    NLTK_DATA=/models/nltk \
    AUDIO_STORAGE_PATH=/data/transcriptions \
    WORKER_ENVIRONMENT=production \
    WORKER_TRANSCRIBER_BACKEND=whisperx \
    WORKER_TRANSCRIBER_MODEL=large-v3-turbo \
    WORKER_TRANSCRIBER_DEVICE=cuda \
    WORKER_TRANSCRIBER_COMPUTE_TYPE=default \
    WORKER_TRANSCRIBER_MODEL_DIRECTORY=/models

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        'ffmpeg=7:*' \
        'libgomp1=14.*' \
        'libpq5=17.*' \
        'libsndfile1=1.2.*' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 worker-long-form-diarization \
    && useradd --uid 10001 --gid worker-long-form-diarization --create-home \
        --shell /usr/sbin/nologin worker-long-form-diarization \
    && mkdir --parents \
        /data/transcriptions \
        /models/alignment \
        /models/cache \
        /models/huggingface \
        /models/nltk \
        /models/pyannote \
        /models/torch \
        /models/whisperx \
    && chown --recursive worker-long-form-diarization:worker-long-form-diarization \
        /models

WORKDIR /opt/worker-long-form-diarization

COPY --from=builder /opt/worker-long-form-diarization/.venv \
    /opt/worker-long-form-diarization/.venv

USER 10001:10001

ENTRYPOINT ["/opt/worker-long-form-diarization/.venv/bin/worker-long-form-diarization"]

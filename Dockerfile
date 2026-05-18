FROM python:3.11-slim

# ffmpeg is required by pydub for audio decoding/conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libolm-dev \
        libolm3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first as a separate layer, saves ~2 GB vs CUDA wheels
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 300 --retries 5 \
    torch \
    torchaudio \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY src ./src

# Mount a volume here to cache the 2.4 GB Parakeet checkpoint across container restarts
ENV NEMO_CACHE_DIR=/models
ENV PYTHONUNBUFFERED=1

# Disable NVIDIA/NeMo telemetry
ENV NEMO_ONE_LOGGER_ENABLED=false
ENV ONE_LOGGER_ENABLED=false
ENV NVIDIA_TF32_OVERRIDE=0
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV DO_NOT_TRACK=1

CMD ["python", "-m", "src.main"]

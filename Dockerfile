FROM python:3.11-slim

WORKDIR /app

# libgomp1 is required by onnxruntime (OpenMP threading)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source (includes templates/ and static/)
COPY app/ app/

# Data and model weights live in mounted volumes, not the image
VOLUME ["/app/data", "/app/models"]

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/argus.db \
    DATA_PATH=/app/data \
    MODELS_PATH=/app/models

EXPOSE 8100

# Bind to 0.0.0.0 so the container port is reachable from the host.
# Local dev (python -m app) defaults to 127.0.0.1.
CMD ["python", "-m", "app", "--host", "0.0.0.0"]

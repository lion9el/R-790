# SocialNet — image de production multi-stage légère et sécurisée
# Build: docker build -t socialnet .
# Run:   docker run -e SOCIALNET_SECRET=... -p 8000:8000 socialnet

# ---------- Stage 1: build extension C + deps ----------
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        libjpeg-dev \
        zlib1g-dev \
        libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
# setuptools requis pour compiler l'extension C (python:slim ne l'embarque plus)
RUN pip install --no-cache-dir setuptools wheel     && pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY c_ext/ ./c_ext/
RUN cd c_ext && python setup.py build_ext --inplace     && ls -la searchengine*.so

# ---------- Stage 2: runtime minimal ----------
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        libwebp7 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/false --uid 10001 appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/c_ext/searchengine*.so ./c_ext/
COPY --from=builder /build/c_ext/searchmodule.c ./c_ext/
COPY --from=builder /build/c_ext/setup.py ./c_ext/

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN mkdir -p backend/uploads backups \
    && chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend:/app/c_ext \
    SOCIALNET_SECRET="" \
    SOCIALNET_BACKUP_DIR=/app/backups

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1

WORKDIR /app/backend
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

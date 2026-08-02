# SocialNet — image de production légère
FROM python:3.12-slim

# Dépendances système pour compiler l'extension C + Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        libjpeg-dev \
        zlib1g-dev \
        libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python d'abord (meilleure utilisation du cache Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code source
COPY c_ext/ ./c_ext/
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.sh .

# Compiler l'extension C de recherche
RUN cd c_ext && python setup.py build_ext --inplace && cd ..

# Dossier d'uploads (persistant via volume en prod)
RUN mkdir -p backend/uploads

# Variables d'environnement par défaut (à surcharger en production)
ENV SOCIALNET_SECRET=""
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Santé simple
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1

WORKDIR /app/backend
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

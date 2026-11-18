# syntax=docker/dockerfile:1

# ---- frontend -------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- runtime --------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDFCONV_JOB_DIR=/data/jobs

# tesseract-ocr-ind provides the Indonesian language data the default
# "ind+eng" pipeline expects. libglib/libgomp/libfreetype back the wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ind \
        libglib2.0-0 \
        libgomp1 \
        libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pdfconv ./pdfconv
COPY api ./api
COPY fixtures ./fixtures
COPY --from=web /web/dist ./web/dist

RUN useradd --system --uid 10001 --home-dir /app appuser \
    && mkdir -p /data/jobs \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# Job state is in memory, so exactly one worker process per container.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

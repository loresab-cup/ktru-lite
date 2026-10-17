FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLOAD_DIR=/data/uploads \
    CACHE_DIR=/cache \
    CATALOG_PATH=/catalog/ktru_catalog.jsonl.gz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
# Volumes mounted on /data and /cache take this ownership on first creation
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /data/uploads /cache \
    && chown -R app /data /cache
USER app

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

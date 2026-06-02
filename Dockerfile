FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway_server.py .
COPY gunicorn.conf.py .
COPY config.py .
COPY celery_worker.py .
COPY api ./api
COPY database ./database
COPY database_init ./database_init
COPY task_queue ./task_queue
COPY utils ./utils

RUN useradd -m -u 1000 gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5.0)" || exit 1

CMD ["gunicorn", "gateway_server:app", "-c", "gunicorn.conf.py"]

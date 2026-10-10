FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Learner code runs in E2B sandboxes in production. A deployment without
# E2B_API_KEY runs it as a subprocess here instead (meant for development),
# so the server never runs as root, and /app stays owned by root so that
# code can't rewrite it.
RUN useradd --create-home --uid 10001 mwalimu
USER mwalimu

# Hosts like Railway set PORT; 8000 otherwise.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"
# Migrations run on every start. They're a no-op when the schema is current,
# and a Postgres advisory lock (migrations/env.py) serialises replicas.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY}"]

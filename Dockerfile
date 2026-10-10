FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Learner code runs in a subprocess of this server until it moves to a
# separate sandbox (#3), so the server never runs as root, and /app stays
# owned by root so that code can't rewrite the server.
RUN useradd --create-home --uid 10001 mwalimu
USER mwalimu

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]

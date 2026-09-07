# Image Playwright officielle : Python 3.12 + Chromium + dépendances système (Ubuntu noble).
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/repondu

COPY pyproject.toml ./
COPY app ./app
COPY data/cities.csv ./data/cities.csv
RUN pip install --no-cache-dir . && playwright install chromium

VOLUME ["/srv/repondu/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

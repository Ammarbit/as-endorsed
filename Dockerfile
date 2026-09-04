# One container: bootstraps the corpus and synthetic data on first start, then serves the API and client.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/data/models AS_ENDORSED_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY web ./web
RUN pip install --upgrade pip && pip install -e ".[llm]"

VOLUME ["/data"]
EXPOSE 8000
CMD ["sh", "-c", "as-endorsed bootstrap && uvicorn as_endorsed.api:app --host 0.0.0.0 --port 8000"]

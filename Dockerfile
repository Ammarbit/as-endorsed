# One image, everything baked in: the public forms, the parsed clause trees, the synthetic
# book of accounts, the endorsement extractions and resolutions, and the ONNX models.
# Cold start is seconds, not the five-minute bootstrap, and the container needs no
# network at runtime unless a Claude generator is configured.
#
#   docker build -t as-endorsed .
#   docker run -p 8000:8000 as-endorsed
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... as-endorsed   # Claude generator + LLM extractor

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AS_ENDORSED_DATA_DIR=/data FASTEMBED_CACHE_PATH=/data/models HF_HUB_DISABLE_TELEMETRY=1 \
    PORT=8000

# Hugging Face Spaces run the container as uid 1000; make that user own everything.
RUN useradd -m -u 1000 app && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src
COPY --chown=app:app web ./web
COPY --chown=app:app corpus ./corpus
RUN pip install --upgrade pip && pip install -e ".[llm]"

USER app
# Bake the data and models into the image layer.
RUN as-endorsed bootstrap

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')" || exit 1
CMD ["sh", "-c", "uvicorn as_endorsed.api:app --host 0.0.0.0 --port ${PORT}"]

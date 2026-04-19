FROM python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf

WORKDIR /app

COPY requirements/runtime.txt ./requirements/runtime.txt
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir --require-hashes -r requirements/runtime.txt

RUN mkdir -p /repo

EXPOSE 8471

ENV VANER_REPO_ROOT=/repo
ENV PYTHONPATH=/app/src

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8471/health')" || exit 1

CMD ["sh", "-c", "python -m vaner.cli.main init --path ${VANER_REPO_ROOT} && python -m vaner.cli.main proxy --path ${VANER_REPO_ROOT} --host 0.0.0.0 --port 8471"]

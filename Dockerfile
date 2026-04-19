FROM python:3.11-slim

WORKDIR /app

# Install Vaner with all optional dependencies
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[all]"

# Default repo is mounted at /repo; users can override via X-Vaner-Repo header
RUN mkdir -p /repo

EXPOSE 8471

ENV VANER_REPO_ROOT=/repo

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8471/health')" || exit 1

CMD ["sh", "-c", "vaner init --path ${VANER_REPO_ROOT} && vaner proxy --path ${VANER_REPO_ROOT} --host 0.0.0.0 --port 8471"]

FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd --gid 1000 airlock && \
    useradd --uid 1000 --gid airlock --shell /bin/bash --create-home airlock

COPY . .

# `uv.lock` is the container's dependency pin set. Pip only bootstraps the
# pinned uv executable; uv then resolves the same core environment as CI/local.
RUN pip install --no-cache-dir "uv==0.11.6" && \
    uv sync --locked --no-dev && \
    . scripts/tool-versions.sh && \
    uv pip install --python .venv/bin/python "$AIRLOCK_SPACY_MODEL_URL" && \
    uv run python scripts/check_docker_dependencies.py

ENV PATH="/app/.venv/bin:$PATH"

# Ensure log directory exists and is writable
RUN mkdir -p /app/logs && chown -R airlock:airlock /app/logs

# Switch to non-root user
USER airlock

EXPOSE 4000

CMD ["python", "-m", "airlock.proxy"]

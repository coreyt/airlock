FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd --gid 1000 airlock && \
    useradd --uid 1000 --gid airlock --shell /bin/bash --create-home airlock

# Install system deps for Presidio's NLP model
RUN pip install --no-cache-dir spacy && \
    python -m spacy download en_core_web_lg

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

# Ensure log directory exists and is writable
RUN mkdir -p /app/logs && chown -R airlock:airlock /app/logs

# Switch to non-root user
USER airlock

EXPOSE 4000

CMD ["python", "-m", "airlock.proxy"]

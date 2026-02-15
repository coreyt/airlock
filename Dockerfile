FROM python:3.12-slim

WORKDIR /app

# Install system deps for Presidio's NLP model
RUN pip install --no-cache-dir spacy && \
    python -m spacy download en_core_web_lg

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 4000

CMD ["python", "-m", "airlock.proxy"]

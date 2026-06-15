# Installation

## From PyPI

```bash
pip install airlock-llm
python -m spacy download en_core_web_lg   # required for PII redaction
airlock init
```

`airlock init` generates `config.yaml`, `.env`, and a `logs/` directory in the current working directory.

## From source (quick setup)

```bash
git clone https://github.com/coreyt/airlock && cd airlock
./scripts/setup.sh
```

This installs Airlock and its dependencies, downloads the spaCy model for PII redaction, and runs `airlock init`. Pass `--pip` to use pip instead of uv.

## Developer setup

```bash
git clone https://github.com/coreyt/airlock && cd airlock
./scripts/setup-dev.sh
```

Everything in the standard setup, plus optional extras, install verification, and a
test suite run. The uv path (`uv sync --all-extras`) installs **all** extras
(`db`, `s3`, `sql`, `metrics`, `tui`, `search`, `vertex`, `aistudio`, `mistral`,
`tracing`, `test`, `docs`); pass `--pip` to use pip instead of uv.

!!! note "Batch provider extras"
    AI Studio (Gemini) batch needs the **`aistudio`** extra, Mistral batch needs
    the **`mistral`** extra, and Vertex AI batch needs the **`vertex`** extra. The
    uv dev setup includes all three; install on their own with
    `pip install 'airlock-llm[aistudio]'` / `'airlock-llm[mistral]'` /
    `'airlock-llm[vertex]'` (or `uv sync --extra mistral`).

## Docker

```bash
docker compose up --build
```

The compose file mounts `config.yaml` read-only and persists logs to `./logs/`.

## Add your API keys

Edit the generated `.env` file and fill in your provider keys:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

You only need keys for the providers you plan to use.

## Start the proxy

```bash
# Option A: TUI dashboard with built-in proxy (recommended)
airlock tui --start

# Option B: proxy only (headless)
airlock start
```

Airlock listens on `http://localhost:4000` by default. Change the port with `AIRLOCK_PORT` in `.env`.

## Verify

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-airlock-change-me" \
  -d '{
    "model": "claude-sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Or press `5` in the TUI for the Basic Chat screen to test interactively.

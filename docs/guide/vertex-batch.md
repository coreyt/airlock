# Vertex AI Gemini (Batch)

Airlock can route Gemini through Google **Vertex AI** in addition to the
Google **AI Studio** (`gemini/`) provider used by the default `gemini-*`
aliases. The reason to bother is **batch**: LiteLLM wires the Batch API
(`POST /v1/files` + `POST /v1/batches`) for the `vertex_ai` provider but **not**
for the AI Studio `gemini/` provider. (Airlock now batches AI Studio Gemini through
its own [Batch Gateway](batch.md#ai-studio-gemini-batch-via-the-airlock-batch-gateway);
this guide covers the **Vertex** path, which uses LiteLLM's native batch with
GCS staging.) Vertex batch is asynchronous, ~50% cheaper, and runs up to ~24h.

The deployments are defined in `config.yaml` under the stable `provider/model`
aliases **`vertex/gemini-3.5-flash`** and **`vertex/gemini-3.1-pro`** (provider
`vertex_ai/…`, served-by token `vertex_ai`). This guide covers the GCP setup they
need.

!!! warning "Shipped Vertex entries are chat-only (`vertex_location: global`)"
    The `vertex/…` aliases ship with `vertex_location: global` and carry **no**
    `airlock_batch` marker, so their published capability is **`endpoints:
    ["chat"]`** — Vertex batch is **region-gated** and is *not* advertised at
    `global`. Airlock only adds `batch` to a `vertex_ai/` model's `endpoints` when
    its `vertex_location` is a real region (not `global`). To batch a Vertex
    Gemini model you must point a `vertex_location` at a region where that model
    resolves (see [§7 Model availability](#7-model-availability-read-this-ids-and-regions-differ)).
    Use the [Airlock Batch Gateway](batch.md#ai-studio-gemini-batch-via-the-airlock-batch-gateway)
    (`aistudio/…`) if you need Gemini batch today.

!!! note "Legacy `-vertex` aliases are deprecated (removed in 0.6.0)"
    The pre-0.5.2 names `gemini-3.5-flash-vertex` / `gemini-3.1-pro-vertex` are
    **dual-listed and still functional** in 0.5.2 (same `litellm_params`), but
    carry `deprecated: true` and are **removed in 0.6.0**. Migrate to the
    `vertex/…` names — see the [old → new alias map](batch.md#old-new-alias-map-052).

!!! warning "Batch bypasses Airlock guardrails"
    The Batch API is a LiteLLM passthrough. Airlock's guardrails (PII
    redaction, keyword, enforcer, …) and smart/cost-tier routing run on the
    `/chat/completions` path, **not** on batch jobs. Do not send data through a
    batch that depends on guardrail enforcement — pre-redact client-side, or use
    the guarded chat path for sensitive content.

## Prerequisites

- A GCP project with **billing enabled**.
- The `gcloud` CLI (Cloud Shell already has it).
- Permission to create service accounts, buckets, and IAM bindings.

Set a couple of shell variables to make the commands copy-pasteable:

```bash
PROJECT=your-gcp-project-id
REGION=us-central1                 # batch jobs need a *regional* location
BUCKET=airlock-vertex-batch-$PROJECT
```

## 1. Enable the APIs

```bash
gcloud services enable aiplatform.googleapis.com storage.googleapis.com --project="$PROJECT"
gcloud config set project "$PROJECT"
```

## 2. Create the service account + grant Vertex access

```bash
gcloud iam service-accounts create airlock-vertex \
  --project="$PROJECT" --display-name="Airlock Vertex batch"

SA="airlock-vertex@$PROJECT.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
```

## 3. Create the GCS bucket + grant access

Batch stages its input/output files in GCS (`GCS_BUCKET_NAME`). The service
account needs **object** read/write; the **Vertex AI service agent** (a
Google-managed identity) writes batch **output**, so grant it too.

```bash
gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" --location="$REGION"

# Your SA: read/write/delete objects (the /v1/files upload step)
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"

# Your SA: bucket metadata read (silences storage.buckets.get 403s)
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.legacyBucketReader"

# Vertex AI service agent: object access for batch OUTPUT writes
PNUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:service-$PNUM@gcp-sa-aiplatform.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

## 4. Create the key and put it on the Airlock host

```bash
gcloud iam service-accounts keys create ~/airlock-vertex.json --iam-account="$SA"
```

!!! warning "The key must live on the machine running Airlock"
    If you ran the steps in **Cloud Shell**, the key is on the Cloud Shell VM,
    not on your Airlock host. Download it and move it onto the Airlock box,
    under the gitignored `secrets/` directory:

    ```bash
    cloudshell download ~/airlock-vertex.json    # then copy to the Airlock host
    ```

    Place it at e.g. `secrets/airlock-vertex.json` in the project (the `secrets/`
    directory is gitignored — never commit the key).

!!! note "Org policy may block JSON keys"
    If key creation fails with `constraints/iam.disableServiceAccountKeyCreation`,
    your org disallows static keys — use Workload Identity / Application Default
    Credentials instead and point `VERTEX_CREDENTIALS` at that.

## 5. Install the `vertex` dependency

LiteLLM's `vertex_ai` provider needs `google-auth` (for token minting and GCS),
which `litellm[proxy]` does not pull in. It's packaged as the `vertex` extra:

```bash
make sync          # uv sync --all-extras + restores the spaCy PII model
# or: uv sync --extra vertex
```

## 6. Configure `.env`

```bash
VERTEX_PROJECT=your-gcp-project-id
VERTEX_LOCATION=global            # see model availability below
VERTEX_CREDENTIALS=/abs/path/to/secrets/airlock-vertex.json
GCS_BUCKET_NAME=airlock-vertex-batch-your-gcp-project-id
```

!!! note "`vertex_location` is pinned in config.yaml"
    The `vertex/…` deployments (and the legacy `gemini-*-vertex` twins) hard-code
    `vertex_location: global` in `config.yaml`, so the `VERTEX_LOCATION` env var is
    **not** consulted for them — change the value in `config.yaml` if you need a different location.
    `VERTEX_PROJECT`, `VERTEX_CREDENTIALS`, and `GCS_BUCKET_NAME` *are* read from
    the environment.

## 7. Model availability (read this — ids and regions differ)

Vertex does not expose every Gemini id in every region, and the ids can carry a
`-preview` suffix that differs from the GA name. Verify against your project
before assuming an id works:

| Model id | Where it resolved (this project) |
|---|---|
| `gemini-3.5-flash` | `global` only (404 in `us-central1`) |
| `gemini-3.1-pro-preview` | `global` only — **GA name `gemini-3.1-pro` is 404** |
| `gemini-2.5-flash`, `gemini-2.5-pro` | `global` and `us-central1` |

!!! warning "Batch wants a regional location"
    Sync completions work on `global`, but Vertex `BatchPredictionJob` generally
    requires a **regional** location. If a model is only available on `global`
    (as the 3.x models are here), batch may be rejected until that model is
    available in a region. For batch today, prefer a model+region pair that
    resolves regionally.

## 8. Restart and verify

```bash
systemctl --user restart airlock
systemctl --user status airlock --no-pager
```

!!! danger "Do not probe `GET /health`"
    `GET /health` fires live completions to every model. Use
    `GET /health/liveliness` for liveness.

Sync smoke test through the proxy:

```bash
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"vertex/gemini-3.5-flash","messages":[{"role":"user","content":"say ok"}],"max_tokens":10}'
```

To list exactly which Gemini ids your project serves (per location), call the
publisher-models endpoint with the SA token — see
`dev/vertex-gemini-batch-setup.md` for a ready-made probe script.

## 9. Run a batch

Once a model resolves in a batch-capable region:

```bash
# 1. Upload the JSONL of requests
curl -s http://localhost:4000/v1/files \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch -F file=@requests.jsonl
# 2. Create the batch with the returned file id
curl -s http://localhost:4000/v1/batches \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"input_file_id":"<file_id>","endpoint":"/v1/chat/completions","completion_window":"24h"}'
# 3. Poll
curl -s http://localhost:4000/v1/batches/<batch_id> \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

# Vertex AI Gemini Batch — Setup & Investigation Notes

**Date:** June 14, 2026
**Context:** Adding provider-managed batch for Gemini through the Airlock/LiteLLM proxy.

This is the developer record behind `docs/guide/vertex-batch.md` — why we went to
Vertex, the exact GCP setup (with this deployment's real values), and the probe
scripts + findings that determined the working config.

## Why Vertex (and not AI Studio) for Gemini batch

LiteLLM only wires the Batch API for a fixed provider set. Verified in the
installed build (`litellm/batches/main.py` if/elif + `get_provider_batches_config`):

- **Wired:** `openai`, `azure`, `vertex_ai`, `anthropic`, `bedrock`.
- **NOT wired:** `gemini` (AI Studio). A `create_batch` on a `gemini/…` model
  raises *"LiteLLM doesn't support custom_llm_provider=gemini for 'create_batch'"*.
  The only `gemini` reference in the batch module is a default cost-calc model name.

Google's AI Studio API *does* have a native Batch Mode (`batchGenerateContent` /
`client.batches.create`), so this is "not wired in LiteLLM," not "unsupported by
Google." But to batch Gemini **through the proxy** today, the route is `vertex_ai`.

## Dependency gap: `google-auth`

`litellm[proxy]` does **not** install `google-auth`. The first vertex call fails
with `litellm.APIConnectionError: No module named 'google'` (from
`vertex_llm_base.py` `_ensure_access_token_async`, which imports `google.auth` /
`google.oauth2`). Fixed by adding a `vertex` optional-dependency:

```toml
# pyproject.toml [project.optional-dependencies]
vertex = ["google-auth>=2.0.0"]
```

LiteLLM does GCS via REST with the OAuth bearer token, so `google-cloud-storage`
is **not** required — `google-auth` alone covers sync + batch staging. Install
with `make sync` (or `uv sync --extra vertex`).

> `uv sync` exact-prunes the out-of-band `en_core_web_lg` spaCy model; the
> PostToolUse hook / `make sync` restore it. See the spaCy-guard commit.

## Auth: VERTEX_CREDENTIALS, not an API key

The vertex **batch** handler (`llms/vertex_ai/batches/handler.py`) authenticates
every op via `_ensure_access_token(credentials=vertex_credentials)` →
`Authorization: Bearer <token>`, and explicitly sets `gemini_api_key=None`. So a
`VERTEX_API_KEY` (Vertex express mode) does **not** work for batch — you need a
**service-account** (`VERTEX_CREDENTIALS` = path to JSON key, or the JSON string).
GCS object ops also require IAM, which an API key can't provide.

## GCP setup (this deployment's actual values)

- Project: `api-5498240749530952133-752265`
- Region: `us-central1` (regional; for batch) / `global` (where 3.x resolve)
- Bucket: `gs://airlock-vertex-batch-5498240749530952133`
- SA: `airlock-vertex@api-5498240749530952133-752265.iam.gserviceaccount.com`
- Key on host: `secrets/airlock-vertex.json` (gitignored)

```bash
PROJECT=api-5498240749530952133-752265
BUCKET=airlock-vertex-batch-5498240749530952133
SA="airlock-vertex@$PROJECT.iam.gserviceaccount.com"

gcloud services enable aiplatform.googleapis.com storage.googleapis.com --project="$PROJECT"
gcloud config set project "$PROJECT"

gcloud iam service-accounts create airlock-vertex --project="$PROJECT" \
  --display-name="Airlock Vertex batch"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"

gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" --location=us-central1
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.legacyBucketReader"

# Vertex AI service agent — writes batch OUTPUT
PNUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:service-$PNUM@gcp-sa-aiplatform.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

gcloud iam service-accounts keys create ~/airlock-vertex.json --iam-account="$SA"
# Cloud Shell: `cloudshell download ~/airlock-vertex.json`, then move to the
# Airlock host under secrets/ (gitignored). The key must live where Airlock runs.
```

`.env` on the Airlock host:

```bash
VERTEX_PROJECT=api-5498240749530952133-752265
VERTEX_LOCATION=global
VERTEX_CREDENTIALS=<repo-root>/secrets/airlock-vertex.json
GCS_BUCKET_NAME=airlock-vertex-batch-5498240749530952133
```

Note: `config.yaml` pins `vertex_location: global` literally on each
`gemini-*-vertex` deployment, so `VERTEX_LOCATION` is not consulted for them.

## Model availability — probe results (2026-06-14)

`generateContent` against each id, per location, with the SA token:

| id | global | us-central1 | us-east5 |
|---|---|---|---|
| `gemini-3.5-flash` | **200** | 404 | 404 |
| `gemini-3-flash-preview` | **200** | 404 | 404 |
| `gemini-3.1-pro-preview` | **200** | 404 | 404 |
| `gemini-3.1-pro` (GA name) | 404 | 404 | 404 |
| `gemini-3-pro-preview` | 404 | 404 | 404 |
| `gemini-2.5-pro`, `gemini-2.5-flash` | 200 | 200 | 404 |

Takeaways:
- 3.x Gemini is **`global`-only** in this project; the GA id `gemini-3.1-pro` is
  404 — Vertex uses the **`-preview`** suffix (`gemini-3.1-pro-preview`).
- 2.5 resolves both globally and in `us-central1`.
- Initial 3.1-pro 404s were an **id/region** issue (and a GCP permission update),
  not a litellm bug.

### Probe script (reusable)

```python
import json, urllib.request, urllib.error
from google.oauth2 import service_account
import google.auth.transport.requests as gtr

PROJECT="api-5498240749530952133-752265"
creds = service_account.Credentials.from_service_account_file(
    "secrets/airlock-vertex.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(gtr.Request()); tok = creds.token
body = json.dumps({"contents":[{"role":"user","parts":[{"text":"hi"}]}],
                   "generationConfig":{"maxOutputTokens":5}}).encode()
host = lambda loc: ("https://aiplatform.googleapis.com" if loc=="global"
                    else f"https://{loc}-aiplatform.googleapis.com")
for loc in ["global","us-central1","us-east5"]:
    base=f"{host(loc)}/v1/projects/{PROJECT}/locations/{loc}/publishers/google/models"
    for m in ["gemini-3.5-flash","gemini-3.1-pro-preview","gemini-2.5-flash"]:
        req=urllib.request.Request(f"{base}/{m}:generateContent",data=body,
            headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"})
        try: urllib.request.urlopen(req,timeout=40); print("200 ",loc,m)
        except urllib.error.HTTPError as e: print(e.code,loc,m)
```

## GCS write test (2026-06-14)

Object-level write/read/delete with the SA against the bucket:

```
bucket GET (metadata)  : 403   (storage.buckets.get — not in objectAdmin; benign for batch)
object WRITE           : 200   ✅ (storage.objects.create — the /v1/files step)
object READ            : 200   ✅
object DELETE          : 204   ✅
```

Conclusion: the batch **write path works**. The 403 is bucket metadata only,
which batch (object URIs) doesn't need; `legacyBucketReader` silences it.
Untested: the Vertex service agent writing batch **output** — granted above,
confirm on the first real batch.

## Open question: batch on `global`

The 3.x models resolve only on `global`, but Vertex `BatchPredictionJob`
generally requires a **regional** location. So 3.x **sync** works now; 3.x
**batch** may be rejected on `global` until the model is regionally available
(or use a 2.5 model in `us-central1` for batch). Settle by submitting one tiny
batch.

## Guardrail caveat

Batch is a LiteLLM passthrough — Airlock guardrails (PII, keyword, enforcer) and
smart/cost-tier routing run on `/chat/completions`, not on batch jobs. Sensitive
data in a batch is **not** redacted. Pre-redact client-side or use the guarded
chat path.

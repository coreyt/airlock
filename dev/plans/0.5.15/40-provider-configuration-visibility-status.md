# Slice 40 — provider configuration visibility status

**Status:** implemented and independently reviewed, 2026-08-16.

The same design-review subagent approved the final cleanup fix after FIX-1
through FIX-4 review cycles; the independent verifier's include-parity finding
was incorporated before that approval.

## Delivered

* A single private, materialized runtime YAML file is now the authority for
  LiteLLM, launcher policy consumers, the LiteLLM child, child Admin policy,
  the immutable provider-configuration projection, and model capability data.
  It is passed through both `--config` and `AIRLOCK_CONFIG`, and removed after
  the child exits or any pre-launch failure.
* `GET /airlock/admin/config/providers` provides a bounded, source-labelled,
  redacted startup snapshot under the new `admin:read_config` scope with
  `Cache-Control: no-store`. Admin stays explicitly opt-in; `admin:read` alone
  is denied and disabled Admin remains 404.
* The projection caps providers/aliases/text fields, omits secrets, references,
  paths, URL userinfo/query, and invalid URL values, and uses a
  credential-blind canonical fingerprint. It classifies `api_key` and
  `vertex_credentials` without exposing field or reference names.
* The Config TUI has a separate read-only, HTTP-only **Configured** tab. Its
  background worker renders success/unavailable state and never reads proxy
  configuration files as a fallback.

## Include-semantic correction

Initial Slice 40 audit/review text assumed pinned LiteLLM was one-level only.
Runtime parity testing demonstrated the actual `ProxyConfig._process_includes`
behavior: an included `include:` list extends the active root list during
iteration, which queues descendants after existing entries. The shared resolver
and canonical materialization deliberately reproduce that behavior. The related
historical FIX disposition records this correction.

## RED/GREEN evidence

* **RED:** new resolver and projection tests first failed with missing module
  imports; later focused RED tests exposed runtime-file authority and URL/
  credential edge cases.
* **GREEN:** resolver parity calls pinned LiteLLM directly; tests cover active
  nested include expansion, order/list extension, malformed list target,
  parent/child canonical mapping and CC-12, runtime cleanup, redaction,
  Vertex credentials, invalid API bases, bounds/fingerprint, Admin scope,
  models seam, and TUI success/unavailable worker paths.

## Verification

* `23 passed` — resolver/projection/startup-validation/bootstrap tests.
* Focused parent authority/CC-12/cleanup tests: `3 passed`.
* Focused TUI configured-panel test: `1 passed`.
* Ruff check/format, `git diff --check`, and `mkdocs build --strict --quiet`
  passed.
* `make ensure-spacy` confirmed the declared model and escalated `make verify`
  passed. (The unprivileged gate could not write uv's cache.)
* The broader non-live `make test` was started; focused Slice 40 coverage is
  recorded above. No live provider calls were made.

## Non-goals retained

No configuration/credential/model CRUD, YAML editing, reload, discovery
enablement, LiteLLM database configuration, generic file access, or second
configuration authority was introduced.

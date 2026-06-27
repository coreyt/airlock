"""Airlock API docs enrichment for the LiteLLM FastAPI proxy."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from airlock.litellm_adapter import resolve_proxy_app

AIRLOCK_DOCS_PATH = "/airlock/docs"
_AIRLOCK_DOCS_MARKER = "x-airlock-docs-enriched"


def _is_chat_like_path(path: str) -> bool:
    return path.endswith("/chat/completions") or path in {
        "/v1/messages",
        "/responses",
        "/responses/compact",
        "/v1/responses",
        "/v1/responses/compact",
        "/openai/v1/responses",
        "/openai/v1/responses/compact",
    }


def _ensure_component_header(
    schema: dict[str, Any],
    name: str,
    *,
    description: str,
    value_type: str = "string",
) -> None:
    components = schema.setdefault("components", {})
    headers = components.setdefault("headers", {})
    headers[name] = {
        "description": description,
        "schema": {"type": value_type},
    }


def _ensure_component_schema(
    schema: dict[str, Any],
    name: str,
    value: dict[str, Any],
) -> None:
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas[name] = value


def _append_parameter_ref(operation: dict[str, Any], ref: str) -> None:
    params = operation.setdefault("parameters", [])
    if any(param.get("$ref") == ref for param in params if isinstance(param, dict)):
        return
    params.append({"$ref": ref})


def _merge_description(operation: dict[str, Any], extra: str) -> None:
    existing = operation.get("description") or ""
    if extra in existing:
        return
    operation["description"] = f"{existing}\n\n{extra}".strip()


def _ensure_airlock_components(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {})
    parameters = components.setdefault("parameters", {})
    parameters["XAirlockClient"] = {
        "name": "X-Airlock-Client",
        "in": "header",
        "required": False,
        "description": (
            "Stable Airlock client identifier used for attribution, provider "
            "protection, per-client rate views, and `no_client` bucketing when absent."
        ),
        "schema": {"type": "string"},
    }

    _ensure_component_header(
        schema,
        "X-Airlock-Model-Override",
        description=(
            "Returned when Airlock routed an unpinned request to a different model than "
            "the one initially requested."
        ),
    )
    _ensure_component_header(
        schema,
        "X-Airlock-Provider-Mode",
        description="Airlock provider-specific response mode. Present for Gemini-family responses.",
    )
    _ensure_component_header(
        schema,
        "X-Airlock-Reasoning-Mode",
        description="Resolved Gemini reasoning mode after Airlock request shaping.",
    )
    _ensure_component_header(
        schema,
        "X-Airlock-Provider-State",
        description=(
            "Airlock classification of Gemini output shape. Values include `text`, "
            "`tool`, `mixed`, `thought_only`, and `empty`."
        ),
    )
    _ensure_component_header(
        schema,
        "X-Airlock-Empty-Text-Success",
        description=(
            "Present on Gemini-family responses when the request succeeded but produced "
            "no user-visible text tokens."
        ),
    )
    _ensure_component_schema(
        schema,
        "AirlockErrorResponse",
        {
            "type": "object",
            "properties": {
                "error": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "type": {"type": "string"},
                        "param": {"type": ["string", "null"]},
                        "code": {"type": ["string", "integer", "null"]},
                    },
                    "required": ["message", "type"],
                }
            },
            "required": ["error"],
        },
    )


def _enrich_metadata_schema(body_schema: dict[str, Any]) -> None:
    properties = body_schema.setdefault("properties", {})
    metadata = properties.setdefault(
        "metadata",
        {
            "anyOf": [
                {"type": "object", "additionalProperties": True},
                {"type": "null"},
            ],
            "default": None,
            "title": "Metadata",
        },
    )
    metadata["description"] = (
        "LiteLLM metadata plus Airlock-specific request semantics. "
        "Use `metadata.airlock.gemini` to hint Gemini request behavior."
    )
    metadata["example"] = {
        "airlock": {
            "gemini": {
                "mode": "balanced",
                "visibility": "final_only",
                "allow_empty_text": False,
            }
        }
    }
    any_of = metadata.get("anyOf") or []
    object_branch = next(
        (
            branch
            for branch in any_of
            if isinstance(branch, dict) and branch.get("type") == "object"
        ),
        None,
    )
    if isinstance(object_branch, dict):
        object_branch.setdefault("properties", {})
        object_branch["properties"]["airlock"] = {
            "type": "object",
            "description": "Airlock semantic controls layered on top of the LiteLLM/OpenAI-compatible body.",
            "properties": {
                "gemini": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": [
                                "balanced",
                                "deep_reasoning",
                                "text_only",
                                "tool_oriented",
                            ],
                            "description": "Preferred Gemini request behavior resolved by Airlock.",
                        },
                        "visibility": {
                            "type": "string",
                            "enum": ["final_only", "provider_native"],
                            "description": "How much Gemini provider state the client expects Airlock to expose.",
                        },
                        "turn_state": {
                            "type": "string",
                            "description": "Optional hint that the client expects Gemini reasoning continuity across turns.",
                        },
                        "allow_empty_text": {
                            "type": "boolean",
                            "description": "Explicitly allow successful Gemini responses that contain no `message.content` text.",
                        },
                    },
                }
            },
            "additionalProperties": True,
        }

    properties.setdefault(
        "reasoning_effort",
        {
            "type": "string",
            "enum": ["disable", "low", "medium", "high"],
            "description": "Advanced Gemini/LiteLLM reasoning control. Explicit client values override Airlock Gemini semantic defaults.",
        },
    )
    properties.setdefault(
        "thinking",
        {
            "type": "object",
            "additionalProperties": True,
            "description": "Advanced Gemini thinking configuration passed through to LiteLLM/provider adapters when supported.",
        },
    )


def enrich_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get(_AIRLOCK_DOCS_MARKER):
        return schema

    _ensure_airlock_components(schema)

    info = schema.setdefault("info", {})
    description = info.get("description") or ""
    docs_note = (
        f"Airlock-specific routing, provider protection, client attribution, and Gemini behavior "
        f"are documented at [{AIRLOCK_DOCS_PATH}]({AIRLOCK_DOCS_PATH})."
    )
    if docs_note not in description:
        info["description"] = f"{description}\n\n{docs_note}".strip()

    paths = schema.setdefault("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            if path == "/health":
                _append_parameter_ref(
                    operation, "#/components/parameters/XAirlockClient"
                )
                operation.setdefault("responses", {}).setdefault(
                    "401",
                    {
                        "description": (
                            "Unauthorized. Airlock health probes require the proxy master key "
                            "when the proxy is configured with one."
                        )
                    },
                )
                _merge_description(
                    operation,
                    "Airlock health probes should send `Authorization: Bearer $AIRLOCK_MASTER_KEY` "
                    "and may send `X-Airlock-Client` for attribution.",
                )
                continue

            if method.lower() != "post" or not _is_chat_like_path(path):
                continue

            _append_parameter_ref(operation, "#/components/parameters/XAirlockClient")
            _merge_description(
                operation,
                "Airlock layers provider protection, pinned-model routing rules, client attribution, "
                "and Gemini semantics on top of the LiteLLM/OpenAI-compatible request surface.",
            )

            request_body = (
                (operation.get("requestBody") or {}).get("content") or {}
            ).get("application/json")
            body_schema = (request_body or {}).get("schema")
            if isinstance(body_schema, dict):
                _enrich_metadata_schema(body_schema)

            responses = operation.setdefault("responses", {})
            success = responses.setdefault(
                "200", {"description": "Successful Response"}
            )
            success_headers = success.setdefault("headers", {})
            success_headers.setdefault(
                "X-Airlock-Model-Override",
                {"$ref": "#/components/headers/X-Airlock-Model-Override"},
            )
            success_headers.setdefault(
                "X-Airlock-Provider-Mode",
                {"$ref": "#/components/headers/X-Airlock-Provider-Mode"},
            )
            success_headers.setdefault(
                "X-Airlock-Reasoning-Mode",
                {"$ref": "#/components/headers/X-Airlock-Reasoning-Mode"},
            )
            success_headers.setdefault(
                "X-Airlock-Provider-State",
                {"$ref": "#/components/headers/X-Airlock-Provider-State"},
            )
            success_headers.setdefault(
                "X-Airlock-Empty-Text-Success",
                {"$ref": "#/components/headers/X-Airlock-Empty-Text-Success"},
            )
            responses.setdefault(
                "429",
                {
                    "description": (
                        "Returned when an upstream provider rate-limits/quota-fails, or when Airlock "
                        "preemptively blocks the request to protect provider standing. Pinned requests "
                        "are not silently switched to other models."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/AirlockErrorResponse"
                            }
                        }
                    },
                },
            )

    schema[_AIRLOCK_DOCS_MARKER] = True
    return schema


def render_airlock_docs_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Airlock Docs</title>
    <style>
      :root {
        --bg: #f5f1e8;
        --ink: #1c2b2d;
        --muted: #57686a;
        --panel: #fffaf0;
        --border: #d8c9a8;
        --accent: #14532d;
      }
      body {
        margin: 0;
        font-family: "Iowan Old Style", "Palatino Linotype", serif;
        background: radial-gradient(circle at top right, #fff4d6, var(--bg) 45%);
        color: var(--ink);
      }
      main {
        max-width: 920px;
        margin: 0 auto;
        padding: 40px 24px 72px;
      }
      h1, h2 { line-height: 1.1; }
      h1 { font-size: 3rem; margin-bottom: 0.25rem; }
      p, li { font-size: 1.05rem; line-height: 1.6; }
      .lede { color: var(--muted); max-width: 52rem; }
      .panel {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 20px 22px;
        margin: 24px 0;
        box-shadow: 0 10px 30px rgba(28, 43, 45, 0.05);
      }
      code, pre {
        font-family: "Berkeley Mono", "SFMono-Regular", monospace;
        background: rgba(20, 83, 45, 0.08);
      }
      code { padding: 0.1rem 0.35rem; border-radius: 6px; }
      pre {
        padding: 16px;
        border-radius: 14px;
        overflow: auto;
        white-space: pre-wrap;
      }
      a { color: var(--accent); }
      .links a { margin-right: 16px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Airlock</h1>
      <p class="lede">
        Airlock keeps the LiteLLM/OpenAI-compatible API surface, but changes request semantics with client attribution,
        provider protection, pinned-model routing rules, model override headers, and Gemini-specific behavior.
      </p>
      <p class="links">
        <a href="/">Swagger</a>
        <a href="/redoc">ReDoc</a>
        <a href="/openapi.json">OpenAPI JSON</a>
      </p>

      <section class="panel">
        <h2>Client Attribution</h2>
        <p>
          Send <code>X-Airlock-Client</code> on requests that should be attributed to a specific caller.
          If omitted, Airlock groups traffic under <code>no_client</code>.
        </p>
        <pre>curl -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \\
  -H "X-Airlock-Client: my-app" \\
  -H "Content-Type: application/json" \\
  http://localhost:4000/v1/chat/completions \\
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'</pre>
      </section>

      <section class="panel">
        <h2>Routing and Provider Protection</h2>
        <p>
          Pinned requests keep the requested model. If the provider is unhealthy or protected, Airlock returns
          <code>429</code> instead of silently switching models. Unpinned requests may be rerouted and will return
          <code>X-Airlock-Model-Override</code> when that happens.
        </p>
      </section>

      <section class="panel">
        <h2>Gemini Semantics</h2>
        <p>
          On <code>/v1/chat/completions</code>, Airlock accepts Gemini hints under
          <code>metadata.airlock.gemini</code>. This keeps the request body OpenAI-compatible while exposing Gemini
          behavior intentionally.
        </p>
        <pre>{
  "model": "gemini-pro",
  "messages": [{"role": "user", "content": "Think carefully, then answer."}],
  "metadata": {
    "airlock": {
      "gemini": {
        "mode": "balanced",
        "visibility": "final_only",
        "allow_empty_text": false
      }
    }
  }
}</pre>
        <p>
          Airlock reports Gemini results with response headers such as
          <code>X-Airlock-Provider-Mode</code>,
          <code>X-Airlock-Reasoning-Mode</code>,
          <code>X-Airlock-Provider-State</code>, and
          <code>X-Airlock-Empty-Text-Success</code>.
        </p>
      </section>
    </main>
  </body>
</html>
"""


def install_airlock_docs(app: FastAPI) -> None:
    if getattr(app.state, "airlock_docs_installed", False):
        return

    @app.get(
        AIRLOCK_DOCS_PATH,
        include_in_schema=True,
        tags=["Airlock"],
        summary="Airlock conceptual docs",
    )
    async def airlock_docs_page() -> HTMLResponse:
        return HTMLResponse(render_airlock_docs_html())

    original_openapi = app.openapi

    def airlock_openapi() -> dict[str, Any]:
        schema = original_openapi()
        return enrich_openapi_schema(schema)

    app.openapi = airlock_openapi  # type: ignore[assignment]
    app.openapi_schema = None
    app.state.airlock_docs_installed = True


def install_airlock_docs_on_proxy_app() -> bool:
    app = resolve_proxy_app()
    if not isinstance(app, FastAPI):
        return False
    install_airlock_docs(app)
    return True

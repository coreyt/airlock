from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from airlock.docs import AIRLOCK_DOCS_PATH, enrich_openapi_schema, install_airlock_docs


def _base_schema() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test Proxy", "version": "1.0.0", "description": "Base docs"},
        "paths": {
            "/v1/chat/completions": {
                "post": {
                    "description": "Base chat route",
                    "parameters": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "model": {"type": "string"},
                                        "messages": {"type": "array"},
                                        "metadata": {
                                            "anyOf": [
                                                {
                                                    "type": "object",
                                                    "additionalProperties": True,
                                                },
                                                {"type": "null"},
                                            ],
                                            "default": None,
                                        },
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {}}},
                        }
                    },
                }
            },
            "/health": {
                "get": {
                    "description": "health",
                    "parameters": [],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
        "components": {"schemas": {}},
    }


def test_enrich_openapi_schema_adds_airlock_extensions() -> None:
    schema = enrich_openapi_schema(_base_schema())

    op = schema["paths"]["/v1/chat/completions"]["post"]
    params = op["parameters"]
    assert {"$ref": "#/components/parameters/XAirlockClient"} in params
    assert "429" in op["responses"]
    assert "headers" in op["responses"]["200"]
    assert "X-Airlock-Model-Override" in op["responses"]["200"]["headers"]

    body_props = op["requestBody"]["content"]["application/json"]["schema"][
        "properties"
    ]
    assert "reasoning_effort" in body_props
    assert "thinking" in body_props
    assert body_props["metadata"]["example"]["airlock"]["gemini"]["mode"] == "balanced"

    health = schema["paths"]["/health"]["get"]
    assert {"$ref": "#/components/parameters/XAirlockClient"} in health["parameters"]
    assert "401" in health["responses"]
    assert AIRLOCK_DOCS_PATH in schema["info"]["description"]


def test_install_airlock_docs_adds_route_and_enriched_openapi() -> None:
    app = FastAPI(title="Test")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat(payload: dict) -> dict:
        return payload

    install_airlock_docs(app)
    client = TestClient(app)

    docs_response = client.get(AIRLOCK_DOCS_PATH)
    assert docs_response.status_code == 200
    assert "Client Attribution" in docs_response.text
    assert "/openapi.json" in docs_response.text

    schema = client.get("/openapi.json").json()
    assert AIRLOCK_DOCS_PATH in schema["paths"]
    assert {"$ref": "#/components/parameters/XAirlockClient"} in schema["paths"][
        "/v1/chat/completions"
    ]["post"]["parameters"]

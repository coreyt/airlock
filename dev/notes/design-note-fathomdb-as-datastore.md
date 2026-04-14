# Architecture Design Note: FathomDB as Airlock's Datastore

**Date:** April 11, 2026  
**Status:** Proposed  
**Author:** Gemini CLI Architecture Agent  

## 1. Executive Summary

This document proposes integrating **FathomDB** (`coreyt/fathomdb`) version 0.3.1 as the canonical local datastore for the Airlock proxy. Airlock currently relies on ephemeral memory for high-speed routing and flat JSONL files for logging, which limits its ability to provide persistent billing, semantic caching, and advanced observability.

FathomDB is a local, Rust-powered datastore built specifically for AI agents. It operates on top of SQLite, providing Graph, Vector, and Full-Text Search capabilities in a single embedded file. Integrating FathomDB satisfies Airlock's complex datastore needs while maintaining its "zero-infrastructure, local-first" design philosophy.

## 2. Why FathomDB 0.3.1 is the Ideal Candidate

FathomDB perfectly aligns with Airlock's architectural constraints and user needs:

*   **Zero DevOps Overhead:** Like SQLite, FathomDB is an embedded database. It requires no separate server, cluster, or container to run.
*   **Graph Backbone:** FathomDB manages nodes, edges, and logical identity with supersession (upserting without direct mutation). This is ideal for modeling Airlock's complex interactions: `(Client) -> [USES] -> (Model) -> [TRIGGERS] -> (Guardrail)`.
*   **Full-Text Search (FTS5):** Built-in FTS5 support allows Airlock's AI Advisor to instantly query historical logs across chunks and node properties, replacing slow JSONL file parsing.
*   **Vector Search:** Integrated natively via `sqlite-vec`. This unlocks the ability to build a **local semantic cache** within Airlock, storing embeddings of previous prompts to short-circuit redundant upstream API calls.
*   **Python SDK (PyO3 bindings):** FathomDB version 0.3.1 provides native PyO3 bindings (`pip install fathomdb`), meaning Airlock can interface with the high-performance Rust engine directly from Python.
*   **Concurrency Model:** FathomDB uses a single-writer / multi-reader execution model with a WAL-backed reader pool. This is crucial for Airlock, as it allows hundreds of concurrent async requests to read from the semantic cache or threat matrix without being blocked by logging writes.

## 3. Proposed Architecture & SDK Integration

### 3.1 SDK Initialization
Airlock will initialize the FathomDB `Engine` during startup and attach it to the application state.
```python
from fathomdb import Engine

# Initialize the engine (WAL-backed reader pool automatically configured)
db_engine = Engine.open("airlock_state.db")
```

### 3.2 The `AirlockFathomLogger` (Callback)
We will introduce a new custom LiteLLM callback, `airlock/callbacks/fathom_logger.py`, which utilizes the FathomDB Python SDK. 
*   **Logging:** On `log_success_event` and `log_failure_event`, the callback will write request/response payloads as structured nodes in FathomDB.
*   **Cost Accumulation:** The callback will extract token usage and cost, writing them using FathomDB's `db.write()` to maintain a durable billing ledger via supersession.
```python
# Example FathomDB Write inside the Logger Callback
db_engine.write([{
    "id": f"req_{call_id}",
    "type": "RequestLog",
    "model": model_name,
    "total_tokens": tokens,
    "cost": calculated_cost
}])
```

### 3.3 Semantic Caching (Guardrail/Middleware)
A new `SemanticCacheGuardrail` will be introduced.
*   **Pre-call:** Generates an embedding for the incoming prompt and performs a vector similarity search in FathomDB using its native `sqlite-vec` integration. If a match is found above a confidence threshold, it returns the cached response, completely bypassing the upstream LiteLLM router.
*   **Post-call:** If no cache hit occurred, the successful response and its prompt embedding are asynchronously written to the graph as a `CachedResponse` node with an attached vector.

### 3.4 AI Advisor Integration
The `advisor/tools.py` module will be updated. Tools like `get_recent_errors` and `get_guard_signals` will execute FathomDB's query compiler to search FTS5 indexes instead of parsing JSONL files:
```python
# Example FathomDB Query inside the Advisor
rows = db_engine.nodes("RequestLog").filter("error_flag = true").limit(50).execute()
```
This will dramatically increase the speed and accuracy of the context provided to the LLM.

### 3.5 TUI Dashboard
The `StateStore` will still maintain a 5-minute sliding window in memory for sub-millisecond routing (e.g., Threat Detector). However, the TUI dashboard's reporting views (like billing, active models, and historical alerts) will run extremely fast SQL/Graph queries against FathomDB to render the UI using the reader pool.

## 4. Implementation Plan

1.  **Dependency Addition:** Add `fathomdb>=0.3.1` to `pyproject.toml` as an optional dependency (e.g., `airlock[db]`).
2.  **Schema Design:** Define the FathomDB graph schema for Airlock (Nodes: `RequestLog`, `Client`, `Model`, `Provider`, `GuardrailSignal`).
3.  **Logger Implementation:** Build `AirlockFathomLogger` and ensure parity with the existing Enterprise JSONL Logger, utilizing `db.write()`.
4.  **Billing API:** Expose a fast Python method to aggregate MTD/YTD costs via FathomDB graph queries.
5.  **TUI Update:** Update the Overview screen to query FathomDB for historical data if the database is configured.
6.  **Semantic Cache:** Implement the vector-backed caching layer utilizing `sqlite-vec` support as a future enhancement phase.
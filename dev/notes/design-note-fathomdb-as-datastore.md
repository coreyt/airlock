# Architecture Design Note: FathomDB as Airlock's Datastore

**Date:** April 11, 2026  
**Status:** Proposed  
**Author:** Gemini CLI Architecture Agent  

## 1. Executive Summary

This document proposes integrating **FathomDB** (`coreyt/fathomdb`) as the canonical local datastore for the Airlock proxy. Airlock currently relies on ephemeral memory for high-speed routing and flat JSONL files for logging, which limits its ability to provide persistent billing, semantic caching, and advanced observability.

FathomDB is a local, Rust-powered datastore built specifically for AI agents. It operates on top of SQLite, providing Graph, Vector, and Full-Text Search capabilities in a single embedded file. Integrating FathomDB satisfies Airlock's complex datastore needs while maintaining its "zero-infrastructure, local-first" design philosophy.

## 2. Why FathomDB is the Ideal Candidate

FathomDB perfectly aligns with Airlock's architectural constraints and user needs:

*   **Zero DevOps Overhead:** Like SQLite, FathomDB is an embedded database. It requires no separate server, cluster, or container to run.
*   **Graph Backbone:** FathomDB manages nodes, edges, and logical identity with supersession (upserting without direct mutation). This is ideal for modeling Airlock's complex interactions: `(Client) -> [USES] -> (Model) -> [TRIGGERS] -> (Guardrail)`.
*   **Full-Text Search (FTS5):** Built-in FTS5 support allows Airlock's AI Advisor to instantly query historical logs across chunks and node properties, replacing slow JSONL file parsing.
*   **Vector Search:** Integrated via `sqlite-vec`. This unlocks the ability to build a **local semantic cache** within Airlock, storing embeddings of previous prompts to short-circuit redundant upstream API calls.
*   **Operational State & Append-Only Logs:** FathomDB enforces strict validation contracts and append-only logs for operational state. This provides the durable ledger needed to track Year-to-Date (YTD) and Month-to-Date (MTD) billing per provider/client safely across reboots.
*   **Python SDK:** FathomDB provides native PyO3 bindings (`pip install fathomdb`), meaning Airlock can interface with the high-performance Rust engine directly from Python with full async compatibility.

## 3. Proposed Architecture

### 3.1 The `AirlockFathomLogger` (Callback)
We will introduce a new custom LiteLLM callback, `airlock/callbacks/fathom_logger.py`, which utilizes the FathomDB Python SDK. 
*   **Logging:** On `log_success_event` and `log_failure_event`, the callback will write request/response payloads as structured nodes in FathomDB.
*   **Cost Accumulation:** The callback will extract token usage and cost, writing them to FathomDB's operational append-only log to maintain a durable billing ledger.

### 3.2 Semantic Caching (Guardrail/Middleware)
A new `SemanticCacheGuardrail` will be introduced.
*   **Pre-call:** Generates an embedding for the incoming prompt (using a fast, local embedding model) and performs a vector similarity search in FathomDB. If a match is found above a confidence threshold, it returns the cached response, completely bypassing the upstream LiteLLM router.
*   **Post-call:** If no cache hit occurred, the successful response and its prompt embedding are asynchronously saved back to FathomDB for future use.

### 3.3 AI Advisor Integration
The `advisor/tools.py` module will be updated. Tools like `get_recent_errors` and `get_guard_signals` will query FathomDB's FTS5 indexes instead of parsing JSONL files. This will dramatically increase the speed and accuracy of the context provided to the LLM.

### 3.4 TUI Dashboard
The `StateStore` will still maintain a 5-minute sliding window in memory for sub-millisecond routing (e.g., Threat Detector). However, the TUI dashboard's reporting views (like billing, active models, and historical alerts) will run extremely fast SQL/Graph queries against FathomDB to render the UI.

## 4. Implementation Plan

1.  **Dependency Addition:** Add `fathomdb` to `pyproject.toml` as an optional dependency (e.g., `airlock-llm[db]`).
2.  **Schema Design:** Define the FathomDB graph schema for Airlock (Nodes: `Request`, `Response`, `Client`, `Model`, `Provider`, `GuardrailSignal`).
3.  **Logger Implementation:** Build `AirlockFathomLogger` and ensure parity with the existing Enterprise JSONL Logger.
4.  **Billing API:** Expose a fast Python method to aggregate MTD/YTD costs from FathomDB's operational state.
5.  **TUI Update:** Update the Overview screen to query FathomDB for historical data if the database is configured.
6.  **Semantic Cache:** Implement the vector-backed caching layer as a future enhancement phase.
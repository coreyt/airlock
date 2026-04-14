# Architecture Design Note: FathomDB as Airlock's Datastore

**Date:** April 11, 2026  
**Status:** Proposed  
**Author:** Gemini CLI Architecture Agent  

## 1. Executive Summary

This document proposes integrating **FathomDB** (`coreyt/fathomdb`) version 0.3.1 as an **opt-in**, canonical local datastore for the Airlock proxy. Airlock currently relies on ephemeral memory for high-speed routing and flat JSONL files for logging. While this zero-infrastructure approach is excellent, it limits persistent billing, semantic caching, and advanced observability.

FathomDB is a local, Rust-powered datastore built specifically for AI agents. It operates on top of SQLite, providing Graph, Vector, and Full-Text Search capabilities in a single embedded file. Integrating FathomDB satisfies Airlock's complex datastore needs while maintaining its "zero-infrastructure, local-first" design philosophy.

**Crucial Constraint:** JSONL logging and ephemeral state remain the default. FathomDB is an *optional enhancement*. If FathomDB is not enabled, Airlock must degrade gracefully:
*   **Billing:** Resets on restart (no durable ledger).
*   **Semantic Cache:** Disabled.
*   **Advisor:** Falls back to parsing raw JSONL files via grep/Python loops.

## 2. Why FathomDB 0.3.1 is the Ideal Candidate

FathomDB perfectly aligns with Airlock's architectural constraints and user needs:

*   **Zero DevOps Overhead:** Like SQLite, FathomDB is an embedded database. It requires no separate server, cluster, or container to run.
*   **Graph Backbone:** FathomDB manages nodes, edges, and logical identity with supersession (upserting without direct mutation).
*   **Full-Text Search (FTS5):** Built-in FTS5 support allows Airlock's AI Advisor to instantly query historical logs. **Note:** FathomDB uses fixed tokenizers (`porter` stemming, `unicode61`, `remove_diacritics=2`). Airlock's query builder must respect this normalization to ensure symmetric recall.
*   **Python SDK (PyO3 bindings):** Provides native PyO3 bindings (`pip install fathomdb`), meaning Airlock can interface with the high-performance Rust engine directly from Python.
*   **Concurrency Model:** FathomDB uses a single-writer / multi-reader execution model with a WAL-backed reader pool. This allows hundreds of concurrent async requests to read from the semantic cache without being blocked by logging writes.

## 3. The "BYOE" (Bring Your Own Embedder) Strategy

FathomDB 0.3.1 possesses internal embedding capabilities, but the Python SDK presents a read/write asymmetry: Python cannot pass an `InProcess` embedder to the Rust engine, and writing vectors automatically via FathomDB requires shelling out to a subprocess (`admin.regenerate_vector_embeddings`). 

To maintain tight integration and high performance, Airlock will adopt a **BYOE** strategy:
1.  **Generation:** Airlock will generate embeddings *outside* of FathomDB using a fast, local Python library (e.g., `sentence-transformers` mapped to BAAI/bge-small-en-v1.5) or via LiteLLM's `embedding()` API.
2.  **Ingestion:** The raw `[float, float, ...]` array is passed directly into FathomDB during the `db.write()` phase.
3.  **Engine Initialization:** Airlock will initialize FathomDB with `EmbedderChoice = "none"` (`embedder="none"`) to keep the vector branch dormant on the Rust side, manually passing computed vectors for cosine similarity queries.

*Note: The FathomDB virtual table `vec_nodes_active` must have a dimension matching Airlock's chosen embedder (e.g., 384).*

## 4. Performance & Speed Requirements

Because FathomDB will be integrated into Airlock's critical request path (e.g., Semantic Caching occurs *before* LiteLLM routing), strict performance budgets must be validated:
*   **Engine Init:** < 50ms (Boot time impact must be minimal).
*   **Write Latency (Logger):** < 10ms per payload. Writes must be non-blocking to the main proxy event loop (handled via background tasks).
*   **Read Latency (Cache/FTS):** < 5ms for vector similarity or FTS lookups using the WAL reader pool.
*   **Concurrency:** Must sustain 200+ concurrent reads without query degradation.

## 5. Implementation Plan

1.  **Dependency Addition:** Add `fathomdb>=0.3.1` to `pyproject.toml` as an optional dependency (e.g., `airlock[db]`).
2.  **Pre-Flight Benchmarking:** Author and execute `scripts/benchmark_fathomdb.py` to empirically prove FathomDB meets the speed requirements outlined in Section 4.
3.  **Schema Design:** Define the FathomDB graph schema for Airlock (Nodes: `RequestLog`, `Client`, `Model`, `Provider`, `GuardrailSignal`).
4.  **Logger Implementation:** Build `AirlockFathomLogger` and ensure parity with the existing Enterprise JSONL Logger, utilizing `db.write()`. JSONL remains the fallback.
5.  **Billing API:** Expose a fast Python method to aggregate MTD/YTD costs via FathomDB graph queries.
6.  **TUI Update:** Update the Overview screen to query FathomDB for historical data if the database is configured.
7.  **Semantic Cache:** Implement the vector-backed caching layer utilizing the BYOE strategy as a future enhancement phase.
# User Needs: Persistent Datastore

**Date:** April 11, 2026

## The Problem
Airlock's current architecture relies heavily on two storage mechanisms:
1. **High-Speed Memory (`StateStore`)**: The threat detector, circuit breakers, and Smart Router use Python dictionaries to keep latency under 150 microseconds. Because this is ephemeral, all live metrics, threat scores, and running costs reset to zero upon a restart.
2. **Flat JSONL Files**: Request logs and guardrail observations are appended to daily JSONL files.

While this zero-infrastructure approach is excellent for getting started quickly and maintaining a fast proxy layer, it severely limits Airlock's ability to act as a truly intelligent, stateful AI gateway.

## Core Needs

### 1. Persistent Cost & Quota Accumulation (Billing)
**Need:** A durable, append-only ledger to track Year-to-Date (YTD) and Month-to-Date (MTD) token usage and spend per client, per model, and per provider.
**Why:** Because the `StateStore` resets on reboot, the TUI dashboard cannot reliably display long-term billing or enforce month-long provider budgets without a persistent datastore. We need durability without the operational overhead of managing a full PostgreSQL cluster.

### 2. Instant Observability for the AI Advisor
**Need:** Native Full-Text Search (FTS).
**Why:** The `advisor` agent currently uses raw Python loops to parse through megabytes of flat JSONL files to answer questions like *"Why did claude-sonnet fail so often yesterday?"* A datastore with FTS would index every request, response, and error, allowing the Advisor to execute millisecond queries and instantly find anomalies.

### 3. Semantic Caching
**Need:** Vector Search capabilities.
**Why:** If multiple developers ask the same architectural questions, Airlock forwards identical requests to the upstream provider, incurring redundant token costs. By embedding incoming prompts, Airlock could use a vector datastore to implement a local semantic cache. If a new prompt is mathematically similar to a recent one, Airlock could serve the cached response instantly, reducing upstream latency and saving money.

### 4. Threat & Usage Visualization
**Need:** A Graph structure.
**Why:** Airlock extracts "Profiles" for clients and models by aggregating flat data. A graph datastore natively models relationships: `(Client: Alice) -> [USED] -> (Model: GPT-4o) -> [TRIGGERED] -> (Guardrail: PII_Block)`. This allows for instant traversal of interaction chains to visualize complex threat vectors, routing bottlenecks, and team-specific security alerts on the dashboard and offline slow analyzer.

### 5. High-Concurrency Async Reads
**Need:** A non-blocking, multi-reader architecture.
**Why:** Airlock is an asynchronous proxy serving hundreds of concurrent requests. Any database integrated into the critical path (like for Semantic Caching or Threat Detection) must support high-concurrency read access without locking the event loop, while ensuring safe, serialized writes.

## Conclusion
Airlock requires a **local, AI-native datastore** that supports vectors, full-text search, graph relations, and durable ledgers, while adhering to its core design tenet: requiring zero DevOps infrastructure (e.g., a single-node embedded file like SQLite). It must also feature a robust concurrency model (like a WAL-backed reader pool) to prevent proxy deadlocks.
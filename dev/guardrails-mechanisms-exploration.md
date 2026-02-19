# Guardrails Mechanisms Exploration

This document explores guardrail mechanisms for LLM-based agents, moving from
the standard "Gen 1" filtering stack toward advanced techniques — and critically,
maps what is achievable at the **proxy level** (Airlock's architecture) versus
what requires **model weight access**.

---

## The Short Answer

> Do we need to train our own weights or have deep access to model infrastructure?

**No — for most of the advanced mechanisms that matter.**

The techniques that *require* model internals (activation steering, sparse
autoencoder feature clamping, constrained decoding) are research-grade tools
primarily useful for model *builders*, not model *operators*. The techniques
that are production-ready and high-impact — classifier guardrails, embedding
filters, NLI grounding, LLM-as-judge, speculative parallel execution — are
all achievable at the proxy level with external models or API calls.

The notes that prompted this exploration correctly identify the Gen 1 → Gen 2
evolution. But the framing of "internal activations" as the only path forward
understates how much can be done externally. The real frontier for an enterprise
proxy isn't brain surgery — it's **intelligent orchestration** of multiple
cheap, fast, specialized checks.

---

## 1. What Airlock Already Has

Before exploring what's next, here's the current guardrail stack for reference:

| Guard | Type | Stage | Mechanism |
|---|---|---|---|
| PII Guard | Deterministic + NER | `pre_call` | Presidio (regex + spaCy NLP) |
| Keyword Guard | Deterministic | `pre_call` | Case-insensitive substring match |
| Fast Guardian | Heuristic | `pre_call` | Volume spike, rapid-fire, error probing, payload size |
| Circuit Breaker | State machine | `pre_call` | CLOSED → OPEN → HALF_OPEN per model |

This is a solid deterministic foundation. The gaps are in **semantic
understanding** — none of these guards know what the content *means*.

---

## 2. Achievability Map: Proxy-Level vs. Model-Internal

### Achievable at the Proxy Level (No Weight Access)

These mechanisms work by processing text before/after the LLM call, or by
calling external classifier models. They are the practical frontier for Airlock.

#### Tier 1: Fast, Deterministic (microseconds)

Already partially implemented. Extensions would be incremental.

| Mechanism | Latency | What It Does |
|---|---|---|
| Regex PII / secrets scan | μs | Pattern-match SSN, credit cards, API keys |
| Schema validation | μs | Reject malformed JSON, enforce output structure |
| Tool permission allowlists | μs | Block `DROP TABLE`, restrict file system access |
| Request/response size caps | μs | Hard limits on input/output length |
| Canary token injection | μs | Detect data exfiltration by planting trackable tokens |

#### Tier 2: ML Classifiers (10–200ms)

This is the **highest-impact gap** in Airlock today. Small, specialized models
that understand *meaning* — not just patterns — running locally or via fast API.

| Mechanism | Latency | What It Does |
|---|---|---|
| **Prompt injection classifier** | 10–100ms | DeBERTa-v3-base or ModernBERT-large fine-tuned to detect injection attacks. PromptShield (Llama-3.1-8B based) achieves 94.5% TPR at 1% FPR. |
| **Embedding-based topic filter** | 10–50ms | Encode user prompt + system prompt, compare cosine similarity. Off-topic prompts (including jailbreaks) score low against the system prompt embedding. Fine-tuned bi-encoder (jina-embeddings-v2-small-en) achieves 0.99 recall on HarmBench. |
| **Toxicity classifier** | 10–50ms | Pre-trained toxic language detector. Standard BERT/RoBERTa classifiers. |
| **OpenAI Moderation API** | 50–200ms | Free, multimodal (text+images), `omni-moderation-latest` built on GPT-4o. 42% improvement in multilingual performance. Directly supported by LiteLLM as a guardrail provider. |
| **NLI hallucination detection** | 50–200ms | Compare LLM output against source documents. Vectara HHEM-2.1-Open (DeBERTaV3-based) produces 0–1 score where <0.5 indicates hallucination. |
| **Embedding drift monitoring** | 10–50ms | Track embedding distributions over time to detect when model behavior shifts (e.g., banking bot starts giving medical advice). |

Key insight from recent research: **framing off-topic detection as "is the user
prompt relevant to the system prompt"** effectively generalizes to blocking both
jailbreaks and harmful prompts, since those are inherently off-topic. This means
a single embedding-based filter can serve double duty.

#### Tier 3: Classifier Guardrail Models (200ms–3s)

Larger models purpose-built for safety classification. These are the "LLM-shaped"
guardrails — they understand nuance but cost more latency.

| Model | F1 Score | Notes |
|---|---|---|
| **NemoGuard-8B** (NVIDIA) | 84.2 (prompt) | Current benchmark leader. LoRA adapters on Llama-3.1-Instruct. |
| **Llama Guard 3/4** (Meta) | 76.2 (prompt) | 13 unsafe categories. Separate prompts for input vs. output classification. |
| **Granite Guardian 3.3** (IBM) | — | Optional "thinking" mode traces logic behind risk assessments. |
| **ShieldGemma 2** (Google) | 72.0 (prompt) | Multimodal. Simple binary Yes/No verdict. |
| **Roblox Guard 1.0** | SoTA (2025) | New entrant, strong multi-benchmark performance. |

These can be self-hosted (vLLM, TGI) or accessed via NVIDIA NIM microservices.
At 200ms–2s per call, they **must** run in parallel with the main LLM call (see
Section 4 on speculative guardrailing).

#### Tier 4: LLM-as-Judge (500ms–3s)

Use a separate LLM to evaluate safety. Highest intelligence, highest latency.

| Pattern | How It Works |
|---|---|
| **Binary judge** | Prompt: "Is this request asking for harmful content? Answer 0 or 1." Confine output to single token for speed. |
| **Chain-of-thought judge** | Let the judge reason before deciding. Capital One's AAAI 2025 work showed SFT with CoT significantly improves accuracy. |
| **Multi-judge panel** | Average scores from 2–3 judge models for reliability. Run in parallel. |
| **Fine-tuned judge** | SFT a small model specifically for safety classification. Alignment techniques (DPO, KTO) add further improvement with minimal data. |

Practical model choices: Claude Haiku, GPT-4o-mini, or a self-hosted 7–8B model.
The key is **binary output** — don't ask for explanations in production, just 0/1.

**Security warning:** Recent research (ICLR 2026) demonstrated that guardrails
themselves are vulnerable to reverse-engineering attacks, achieving >0.92 rule
matching rate for under $85 in API costs. Defense-in-depth is not optional.

### NOT Achievable at the Proxy Level (Requires Model Weights)

These are the "Gen 2 brain surgery" techniques. They are real and powerful, but
they require control over the model's forward pass — something an API proxy
fundamentally cannot have.

| Mechanism | Why It Requires Weights | Current State |
|---|---|---|
| **Activation steering** | Injects vectors into hidden states during forward pass | Sparse Representation Steering (SRS, March 2025) achieves 100% refusal against malicious instructions. But requires full model access. |
| **SAE feature clamping** | Reads/writes internal neuron activations | Anthropic scaled to millions of features on Claude 3 Sonnet. Still primarily a research tool — 10–40% performance degradation on downstream tasks. |
| **Constrained decoding** | Controls the decoding loop's logit distribution | Outlines/XGrammar enforce grammars. But DictAttack (2025) showed structured output itself can be a jailbreak vector — 94–99% attack success rate. |
| **Constitutional AI training** | Shapes base model behavior during training | Anthropic's core method for Claude. Published updated constitution January 2026. Proxy operators cannot apply this. |
| **SelfGrader (DPL scoring)** | Requires raw logits over numerical tokens | Dual-perspective logit scoring. Some providers expose logprobs, but not full logit manipulation. |
| **Neural circuit breakers** | Maps "misalignment vectors" in activation space | Active 2026 research. Detects deception/power-seeking before execution. Requires representation engineering access. |

**The honest assessment:** These techniques are impressive but have significant
limitations even for model builders. SAEs have 10–40% reconstruction error.
Activation steering can cause unpredictable side effects due to entangled latent
spaces. Constrained decoding has been shown to be an attack surface itself. The
proxy-level techniques are not just "good enough" — they're often more practical
and auditable.

---

## 3. Frameworks Worth Evaluating

### 3.1 Guardrails AI

**What it is:** Python/JS library for composable input/output validation.

**Core model:** Validators from a Hub, chained together. When validation fails:
reask, fix, filter, refrain, or raise exception.

**Relevant validators for Airlock:**
- Toxicity detection
- PII detection
- Jailbreak detection (via Arize AI embeddings)
- Factual consistency (via BespokeLabs MiniCheck)
- JSON/SQL/code validation
- Competitor mention filtering

**Integration path:** LiteLLM has native Guardrails AI integration. Guardrails
AI can run as a server (`guardrails start`) exposing a REST API, which Airlock
could call as a guardrail provider. The Guardrails Index (February 2025)
benchmarks 24 guardrails across 6 categories — useful for selection.

**Fit for Airlock:** High. Schema-enforcement philosophy aligns with Airlock's
deterministic control loop design. The reask loop (Drafter-Auditor pattern) is
already identified in `feature-guardrails-deterministic-control-loops.md`.

### 3.2 NVIDIA NeMo Guardrails

**What it is:** Toolkit using the Colang domain-specific language for dialog
rails. Five rail types: Input, Dialog, Retrieval, Execution, Output.

**Key capabilities:**
- Embedding-based KNN intent matching (all-MiniLM-L6-v2)
- Content safety via NemoGuard-8B, LlamaGuard, third-party APIs
- PII detection via GLiNER-PII
- Agentic security: tool call validation, agent workflow protection
- Parallel rails execution to reduce latency
- OpenTelemetry for observability

**Integration path:** LiteLLM does not have native NeMo integration, but NeMo
can wrap any LLM call. It could run as a middleware layer or be called from a
custom guardrail.

**Fit for Airlock:** Medium. NeMo's strength is conversational flow control
(state machines, canonical forms). This matters less for a proxy handling
stateless API calls. The embedding-based topic filtering and parallel execution
patterns are the most transferable ideas.

### 3.3 LLM Guard (Protect AI)

**What it is:** Open-source (MIT), 15 input scanners + 20 output scanners.

**Relevant scanners:**
- Prompt injection (DeBERTa-v3-base)
- PII anonymization (Presidio + custom patterns)
- Toxicity, bias, secrets, malicious URLs
- Factual consistency, data leakage detection

**Integration path:** REST API mode. Could be deployed alongside Airlock and
called from a custom guardrail.

**Fit for Airlock:** High for scanner coverage. Overlaps with existing Presidio
integration but adds prompt injection detection and output scanning that Airlock
currently lacks.

### 3.4 Provider APIs (Free / Low-Cost)

| Provider | API | Cost | Capabilities |
|---|---|---|---|
| **OpenAI** | Moderation API (`omni-moderation-latest`) | Free | Text+images, 11 categories, calibrated scores, 40 languages |
| **Azure** | AI Content Safety + Prompt Shields | Pay-per-use | Violence/hate/sexual/self-harm + indirect prompt injection detection |
| **Anthropic** | No standalone moderation API | N/A | Use Claude itself as classifier (costs standard API pricing) |

OpenAI's Moderation API is the low-hanging fruit — free, multimodal, already
supported by LiteLLM as a guardrail provider. The main limitation is it only
covers broad content safety categories, not domain-specific policies.

---

## 4. Architectural Patterns for Airlock

### 4.1 Speculative Guardrailing (Parallel Execution)

The single most impactful architectural change. Instead of running all guardrails
sequentially before the LLM call, run expensive checks **in parallel** with the
main call.

```
Client Request
      │
      ▼
[Fast Pre-Call Guards]  ← Regex PII, keywords, rate limit (μs)
      │
      ├──────────────────────────────┐
      │                              │
      ▼                              ▼
[Main LLM Call]              [Parallel Guards]
  (streaming)                  ├─ Prompt injection classifier
      │                        ├─ OpenAI Moderation API
      │                        ├─ Embedding topic filter
      │                        └─ Classifier guardrail model
      │                              │
      ▼                              ▼
[Stream to client]           [Guard verdict arrives]
      │                              │
      ▼                              │
  If guard BLOCKS ──────────────────►│
     │                               │
     ▼                               │
  Cut stream, return                 │
  retraction/error                   │
      │                              │
      ▼                              ▼
  If guard PASSES: stream completes normally
      │
      ▼
[Post-Call Guards]  ← Output PII scan, hallucination check
      │
      ▼
Response to Client
```

**LiteLLM supports this** via `during_call` guardrail mode. Airlock could add a
new guardrail registered as `during_call` that orchestrates parallel checks.

**Trade-off:** If the parallel guard blocks after 500ms of streaming, the client
has already received partial output. The proxy must cut the stream and send a
retraction. This is acceptable for safety-critical blocks but annoying for
borderline cases. A tiered approach works: high-confidence blocks from fast
classifiers stop the call early; lower-confidence signals from LLM judges
trigger post-call review rather than stream cuts.

### 4.2 Layered Escalation

Not every request needs every check. Use fast, cheap guards as a first pass
and only escalate to expensive checks when there's ambiguity.

```
Request arrives
      │
      ▼
[Layer 1: Deterministic]  ← Regex, keywords, allowlists  (μs)
      │
      ├─ CLEAR BLOCK → reject immediately
      ├─ CLEAR PASS  → skip to LLM call
      └─ AMBIGUOUS   → escalate
            │
            ▼
[Layer 2: Fast Classifier]  ← DeBERTa, embedding filter  (10–100ms)
            │
            ├─ CLEAR BLOCK → reject
            ├─ CLEAR PASS  → proceed
            └─ AMBIGUOUS   → escalate
                  │
                  ▼
[Layer 3: LLM Judge]  ← Claude Haiku, GPT-4o-mini  (500ms–2s)
                  │
                  ├─ BLOCK → reject
                  └─ PASS  → proceed
```

This reduces average latency significantly. In practice, 80%+ of requests are
clearly benign and pass Layer 1 with zero ML overhead. Only genuinely suspicious
requests pay the full cost.

**Implementation:** Each layer returns a verdict with a confidence score. The
escalation threshold is configurable per deployment. High-security environments
set low thresholds (escalate more); developer-facing proxies set high thresholds
(escalate less, tolerate more false negatives).

### 4.3 Tool-Call Sandboxing

For agentic workflows where the LLM calls tools, the proxy intercepts the tool
call and enforces policy before execution.

```
LLM requests: {"tool": "bash", "args": {"command": "rm -rf /"}}
      │
      ▼
[Tool Policy Engine]
  ├─ Is tool "bash" allowed for this API key?
  ├─ Is command pattern in allowlist?
  ├─ Does it match any blocklist pattern?
  ├─ Does it exceed resource limits (file size, network access)?
  └─ Verdict: BLOCK
      │
      ▼
Return error to LLM, log attempt
```

This is distinct from content safety — it's about **action safety**. Airlock's
Fast Guardian already does some of this (threat detection, circuit breaking), but
a dedicated tool-call policy engine would handle the agentic case explicitly.

**Relevant framework:** NeMo Guardrails' Execution Rails and IPIGuard's
task-graph-based planning both address this. LangGraph's human-in-the-loop
pattern adds manual approval for high-stakes actions.

### 4.4 Multi-Turn State Tracking

A single request may be safe, but a *sequence* of requests can constitute an
attack (e.g., progressively extracting a system prompt through 20 innocuous
questions). Stateful guardrails track conversation history per client.

| Signal | Detection Method |
|---|---|
| System prompt extraction | Track if responses increasingly mirror the system prompt |
| Progressive jailbreak | Embedding drift across turns — conversation moves toward forbidden topics |
| Credential fishing | Pattern: "What API keys do you have access to?" across variants |
| Context window stuffing | Cumulative token count growing abnormally for a session |

**Implementation:** Airlock's Fast subsystem already tracks per-client state
(`ClientState` with request history). Extending this with per-session embedding
trajectories would enable multi-turn detection. The Slow analyzer could
retrospectively identify multi-turn attack patterns in logs and generate
signatures for the Fast path.

---

## 5. The False Positive Problem

The notes so far focus on catching bad content. In production, the dominant
problem is the opposite: **blocking good content**.

> With 5 guards at 90% accuracy each, assuming independence, the probability of
> at least one false positive is 1 − 0.9⁵ ≈ 41%.

This is the practical ceiling on guardrail stacking. Mitigations:

| Strategy | How It Helps |
|---|---|
| **Tiered escalation** (Section 4.2) | Most requests skip expensive checks entirely |
| **Confidence thresholds** | Only block when classifier confidence > 0.95, not 0.5 |
| **Allowlisting known-safe patterns** | Developer tool signatures, common code patterns |
| **Per-team guardrail profiles** | Security research team gets different thresholds than marketing |
| **Feedback loops** | Log all "near-miss" blocks; slow analyzer identifies false positive patterns |
| **Soft blocks** | Instead of rejecting, tag the request and let downstream decide |

Airlock's existing architecture (per-key metadata, enterprise logger, slow
analyzer) already provides the infrastructure for feedback-driven threshold
tuning. The missing piece is the classifier layer that produces the confidence
scores.

---

## 6. Concrete Next Steps for Airlock

Ordered by impact-to-effort ratio:

### Step 1: OpenAI Moderation API Integration (Lowest Effort)

- LiteLLM already supports this as a guardrail provider
- Free, multimodal, covers broad content safety
- Register as a `during_call` guard to run in parallel
- Provides calibrated scores for all categories
- Effort: configuration change + thin wrapper for logging

### Step 2: Embedding-Based Topic Filter (High Impact)

- Self-host `sentence-transformers/all-MiniLM-L6-v2` (80MB model)
- On each request: encode user prompt, compare against system prompt embedding
- Cosine similarity below threshold → flag as off-topic / potential jailbreak
- 10–50ms latency, catches attacks that keyword filters miss
- Doubles as jailbreak detection (off-topic framing generalizes)
- Effort: new guardrail module + embedding model deployment

### Step 3: Prompt Injection Classifier (High Impact)

- Self-host DeBERTa-v3-base fine-tuned for prompt injection
- Or use LLM Guard's prompt injection scanner as a sidecar
- 10–100ms latency, catches injection attacks that semantic filters miss
- Effort: new guardrail module + classifier deployment

### Step 4: Speculative Guardrailing Architecture

- Add `during_call` guardrail mode to Airlock
- Orchestrate parallel execution of Steps 1–3 alongside the main LLM call
- Implement stream cutting for high-confidence blocks
- Effort: architectural change to guardrail pipeline

### Step 5: NLI Grounding Check (For RAG Use Cases)

- Self-host HHEM-2.1-Open or Bespoke-MiniCheck
- Compare LLM output against retrieved context (if visible in the request)
- Flag unsupported claims as hallucinations
- Run as `post_call` guard
- Effort: new guardrail module + NLI model deployment

### Step 6: LLM-as-Judge (For High-Security Deployments)

- Use Claude Haiku or GPT-4o-mini as a safety judge
- Binary output (0/1) with structured prompt
- Run in parallel (`during_call`) for latency
- Reserve for requests that pass classifier checks but have ambiguous scores
- Effort: new guardrail module + API costs

---

## 7. Framework Comparison for Airlock Integration

| Criterion | Guardrails AI | NeMo Guardrails | LLM Guard | Direct Implementation |
|---|---|---|---|---|
| LiteLLM integration | Native | None (needs wrapper) | REST API | Custom `CustomGuardrail` |
| Validator coverage | Broad (Hub) | Focused (safety + dialog) | Broad (35 scanners) | Build what you need |
| Latency control | Good (remote inference) | Good (parallel rails) | Good (REST) | Full control |
| Reask/retry loops | Built-in | Built-in (Colang) | No | Must implement |
| Agentic tool safety | Limited | Strong (Execution Rails) | Limited | Must implement |
| Observability | Basic | OpenTelemetry | Basic | Airlock's existing logging |
| Operational complexity | Medium (server mode) | High (Colang DSL) | Low (sidecar) | Low |

**Recommendation:** Start with direct implementation of Steps 1–3 (they're
simple enough to not need a framework). Evaluate Guardrails AI for Step 5
(its MiniCheck validator handles NLI) and NeMo for Step 6 (its parallel
execution and agentic patterns are mature).

---

## 8. Security Model: Defense-in-Depth

No single guardrail is sufficient. The stack should be designed so that each
layer catches what the previous one misses:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 0: Network / Auth                                 │
│   API key validation, TLS, rate limiting                │
├─────────────────────────────────────────────────────────┤
│ Layer 1: Deterministic (existing)                       │
│   PII regex, keyword blocklist, request size limits     │
├─────────────────────────────────────────────────────────┤
│ Layer 2: Statistical (new — Tier 2)                     │
│   Embedding topic filter, prompt injection classifier,  │
│   toxicity scorer, OpenAI Moderation API                │
├─────────────────────────────────────────────────────────┤
│ Layer 3: Semantic (new — Tier 3/4)                      │
│   Classifier guardrail model, LLM-as-judge              │
├─────────────────────────────────────────────────────────┤
│ Layer 4: Output Validation (new)                        │
│   NLI grounding, output PII scan, schema validation     │
├─────────────────────────────────────────────────────────┤
│ Layer 5: Behavioral (existing Fast subsystem)           │
│   Volume spike, rapid-fire, error probing, circuit break│
├─────────────────────────────────────────────────────────┤
│ Layer 6: Retrospective (existing Slow subsystem)        │
│   Offline log analysis, trend detection, hypothesis gen │
│   → feeds back into Layers 1–3 threshold tuning         │
└─────────────────────────────────────────────────────────┘
```

The key insight: Layers 5 and 6 (Airlock's Fast/Slow subsystems) are
**already built** and are a competitive advantage. Most guardrail frameworks
focus on Layers 1–4 and ignore the behavioral and retrospective layers entirely.
Airlock's architecture is well-positioned to close the Loop: Slow analysis
identifies patterns → generates hypotheses → tunes Fast/classifier thresholds
→ reduces false positives over time.

---

## 9. What the "Gen 2" Techniques Actually Mean for a Proxy

Revisiting the user's Gen 1 / Gen 2 table with a proxy-operator lens:

| Gen 2 Concept | Proxy-Level Equivalent | Achievable? |
|---|---|---|
| "Internal activations" | External classifier models that approximate the same detection | Yes — NemoGuard-8B, Llama Guard achieve 76–84 F1 |
| "Vector steering" | Embedding-based semantic filtering on inputs/outputs | Yes — bi-encoder topic filters achieve 0.99 recall |
| "NLI checks" | Post-call NLI models comparing output to source documents | Yes — HHEM, MiniCheck, HaluGate |
| "Parallel / speculative" | `during_call` guardrails running alongside the LLM | Yes — LiteLLM supports this natively |
| "Sparse autoencoder features" | Not possible externally | No — requires model internals |
| "Activation steering" | Not possible externally | No — requires model internals |

**The bottom line:** 4 out of 6 "Gen 2" capabilities are achievable at the proxy
level. The 2 that aren't (SAE features, activation steering) are still research-
grade with significant limitations even for model builders. An enterprise proxy
that implements Layers 1–4 well, with parallel execution and feedback loops, is
at the practical state of the art.

---

## 10. Implementation Status

### Semantic Guard Orchestrator (Implemented)

The thin orchestration layer is live at `airlock/guardrails/semantic.py`,
registered as `airlock-semantic-guard` in `config.yaml` with `mode: during_call`.

**What it does:**
- Runs as a `during_call` guardrail — executes in parallel with the LLM call
  via LiteLLM's `asyncio.gather` pattern, so classifier latency is hidden
- Orchestrates pluggable classifiers concurrently via `asyncio.gather`
- Attaches all classifier verdicts (scores, thresholds, labels, durations,
  errors) to request `metadata["airlock_semantic"]` for downstream logging
- Blocks only when any single classifier exceeds its own threshold
- Handles classifier errors gracefully (fail-open by default, configurable
  via `AIRLOCK_SEMANTIC_BLOCK_ON_FAIL`)

**What it logs (the learning signal):**

Every request that passes through the orchestrator produces metadata like:

```json
{
  "airlock_semantic": {
    "status": "passed",
    "blocking_classifier": null,
    "total_duration_ms": 45.2,
    "results": [
      {
        "name": "topic_filter",
        "score": 0.12,
        "threshold": 0.5,
        "blocked": false,
        "label": "on_topic",
        "duration_ms": 23.1
      },
      {
        "name": "injection_detector",
        "score": 0.34,
        "threshold": 0.8,
        "blocked": false,
        "label": "benign",
        "duration_ms": 41.7
      }
    ]
  }
}
```

This metadata flows through the enterprise logger into JSONL logs, where the
slow analyzer can compute:
- Score distributions per classifier (what do real requests look like?)
- False positive/negative rates at different thresholds
- Latency overhead per classifier
- Correlation between classifiers (do they agree? disagree?)
- Which requests are "ambiguous" (close to threshold) — candidates for
  future escalation to LLM-as-judge

**Pluggable classifier interface:**

```python
class Classifier(Protocol):
    @property
    def name(self) -> str: ...
    async def classify(self, text: str) -> ClassifierResult: ...
```

Register classifiers at startup:

```python
from airlock.guardrails.semantic import register_classifier
register_classifier(MyEmbeddingFilter())
register_classifier(MyInjectionDetector())
```

**Next steps:**
- Implement embedding-based topic filter as a `Classifier`
- Implement prompt injection classifier as a `Classifier`
- Extend slow analyzer with `airlock_semantic` dimension analysis
- Use collected data to determine whether cross-classifier score
  aggregation or escalation logic is needed

**Tests:** 41 tests in `tests/test_semantic_guard.py` covering:
- Text extraction, registry management, fail-open/fail-closed
- Concurrent classifier execution (verified parallel, not sequential)
- Error isolation (one crashed classifier doesn't block others)
- Metadata attachment (scores, durations, errors logged on every request)
- Block behavior (raises ValueError, but writes metadata first)

---

## References

### Classifier Guardrail Models
- Llama Guard 3/4 (Meta) — 13 unsafe categories, 76.2 F1
- NemoGuard-8B (NVIDIA) — benchmark leader at 84.2 F1
- ShieldGemma 2 (Google) — multimodal, binary verdict
- Granite Guardian 3.3 (IBM) — optional reasoning traces

### Prompt Injection Detection
- PromptShield (Llama-3.1-8B) — 94.5% TPR at 1% FPR
- deepset prompt injection classifier — DeBERTa-based
- DataSentinel — game-theoretic adversarial training
- IPIGuard — task-graph isolation for agentic systems

### Hallucination Detection
- Vectara HHEM-2.1-Open — DeBERTaV3-based, 0–1 scoring
- Bespoke-MiniCheck — compact factual consistency checker
- HaluGate (vLLM project, Dec 2025) — token-level detection

### Embedding-Based Filtering
- Chua et al. (2025) — off-topic guardrails via bi-encoder, 0.99 recall
- NeMo Guardrails — all-MiniLM-L6-v2 for KNN intent matching

### Frameworks
- Guardrails AI v0.8.1 — composable validators, LiteLLM integration
- NVIDIA NeMo Guardrails — Colang DSL, parallel rails, OpenTelemetry
- LLM Guard (Protect AI) — 35 scanners, REST API, MIT license
- OpenAI Moderation API — free, multimodal, `omni-moderation-latest`
- Azure AI Content Safety — Prompt Shields with Spotlighting (2025)

### Activation Steering / Interpretability (Model-Internal)
- Sparse Representation Steering (SRS, March 2025) — 100% refusal, composable
- Anthropic SAE scaling — millions of features on Claude 3 Sonnet
- Anthropic Attribution Graphs (March 2025) — ~25% prompt coverage
- DictAttack (2025) — structured output as jailbreak vector, 94–99% ASR

### Security
- ICLR 2026 — guardrails reverse-engineered for <$85 in API costs
- Pangea 2025 challenge — 10% of 300k injection attempts bypassed basic filters

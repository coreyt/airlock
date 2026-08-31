# Design (draft): conversation text extraction must cover nested content blocks

**Date:** 2026-08-28
**Status:** Draft design. **Not accepted scope.** Contains a *verified* guardrail
gap with security impact (§1), which is why this is filed ahead of any routing
work. Security review and HITL approval are gates before implementation.
**Scope:** `airlock/text_extract.py`, `airlock/guardrails/pii_guard.py`,
`airlock/fast/router.py`, tests. No endpoint, config-ownership, persistence, or
dependency change.
**Origin:** the lead recorded in
[design-switchyard-routing-evaluation.md](design-switchyard-routing-evaluation.md) §7,
now verified.
**Related:** [design-note-pii-rehydration.md](../design-note-pii-rehydration.md)
(response-side tool-call arguments — complementary, not superseded).

---

## 1. Verified finding

Airlock's text extraction and PII scrub both walk message content with the same
rule: take `type == "text"` blocks, pass everything else through untouched
(`text_extract.py:extract_text_from_messages`,
`pii_guard.py:_scrub_messages:234`). On the OpenAI wire that is adequate,
because a tool reply arrives as `role: "tool"` with **string** content, which the
`isinstance(content, str)` branch catches. On the Anthropic wire it is not: a
tool reply arrives as a `tool_result` **block nested inside a user message**, and
its text is neither scanned nor redacted.

That wire shape is not hypothetical. `airlock/docs.py:18` advertises
`/v1/messages`; `dev/dogfooding.md` routes Claude Code through it via
`ANTHROPIC_BASE_URL`; and LiteLLM 1.94.1 hands the guardrails the **raw**
Anthropic body — `anthropic_endpoints/endpoints.py` reads the request body and
calls `base_process_llm_request(route_type="anthropic_messages")`, which invokes
`proxy_logging_obj.pre_call_hook(data=self.data, call_type=route_type)`
(`common_request_processing.py:1250`) *before* any translation to provider
format. Neither `AirlockPIIGuard` nor `AirlockFastGuardian` gates on
`call_type`, so both run — on a shape they cannot read.

Measured, same conversation on both wires, PII and a blocklist term inside the
tool result:

| | Anthropic Messages wire | OpenAI chat wire |
|---|---|---|
| `extract_text_from_messages` returns | `'check the customer file'` | full text incl. tool output |
| Blocklist keyword visible to scanners | **no** | yes |
| Email / card visible to scanners | **no** | yes |
| Email / card still present verbatim after `_scrub_messages` | **yes** | no |

So on the Claude Code path: **PII redaction does not apply to tool-result
content, keyword blocking does not see it, and the threat detector does not
score it.** Tool results are where file contents, query results, and log dumps
live — the densest PII in the conversation — and they egress to the provider
unredacted. This contradicts the README's "Every request/response logged" and
"Presidio redacts configured entity types" claims for that path.

`tool_use` input carries the same exposure in the other direction: a `Write` or
`Edit` tool call embeds the file body in `input`, and `_scrub_messages` never
descends into it.

**Not yet established, and required before a severity claim is published:**
whether an equivalent gap exists on `/v1/responses`, on the MCP path (which has
its own `_scrub_mcp_arguments` traversal), and whether the JSONL/FathomDB
recorders serialize the unredacted blocks to disk. Item 1 of §6 covers this.

## 2. Why one seam, not three patches

Three call sites share the defect because they share one rule, and the rule is
right — it is the *block vocabulary* that is incomplete. Patching
`_extract_text` for the router alone would fix the least important consumer and
leave the security gap open. The fix belongs in `airlock/text_extract.py`, which
already exists precisely as the "single entry point so that both LLM completions
and MCP tool calls are scanned through the same pipeline".

Two distinct capabilities are needed and must not be conflated:

- **Read coverage** — flatten every text-bearing block for scanning. Pure,
  additive, no request mutation. Low risk.
- **Write coverage** — redact in place inside nested blocks. Mutates what
  egresses. Higher risk, needs its own rollout.

They ship as separate slices with separate gates.

## 3. Draft requirements

### Read coverage

- **DFR-41 — one block vocabulary.** A single documented traversal enumerates
  the text-bearing shapes Airlock accepts and is shared by every consumer. At
  minimum: string content; `text` blocks; `tool_result` blocks whose `content`
  is a string **or** a list of blocks (recurse one level); `tool_use` /
  `function` call arguments; and OpenAI `role: "tool"` string content. Unknown
  block types are skipped, never guessed at, and counted.
- **DFR-42 — role fidelity is preserved.** Flattening must not erase provenance.
  `extract_direct_input` deliberately restricts Phase A injection
  classification to `_DIRECT_INPUT_ROLES` and documents tool results as
  *indirect* Phase B input; broadening the blob extractor must not silently
  broaden the injection classifier's input set. The two seams stay distinct and
  the existing exclusion comment stays true.
- **DFR-43 — bounded work.** Traversal depth, per-block size, and total scanned
  characters are bounded, with the truncation recorded. A 300-message agent run
  with megabyte tool outputs must not turn a guardrail into a latency incident.
  Recursion is depth-limited, not open-ended.
- **DFR-44 — observability of coverage.** Each request records how many blocks
  were scanned and how many were skipped as unknown, so a future wire-format
  change shows up as a rising skip count rather than as silence. Counts only —
  never block content.

### Write coverage (separate slice)

- **DFR-45 — redaction reaches nested text.** The PII scrub redacts
  `tool_result` text and tool-call arguments through the same vocabulary as
  DFR-41, preserving block structure, `tool_use_id` / `tool_call_id` pairing,
  and JSON validity of arguments. A redaction that breaks tool-call pairing is a
  failure, not a tradeoff.
- **DFR-46 — staged rollout.** Nested redaction ships **observe-first**: count
  and report what *would* be redacted, enforce only on an explicit promotion,
  following the 0.5.12 PII-egress precedent and its measurement protocol. The
  fail-open contract is unchanged — a Presidio failure leaves the request
  byte-identical.
- **DFR-47 — rehydration boundary is respected.** Nested redaction must not
  widen the reverse-map beyond what
  [design-note-pii-rehydration.md](../design-note-pii-rehydration.md) bounds. If
  a nested placeholder cannot be rehydrated, that is stated, not discovered.

## 4. Draft acceptance criteria

- **DAC-34** — a fixture conversation on **both** wire shapes yields the same
  scanned text, the same keyword-block verdict, and the same threat score. This
  is the regression test the gap would have failed; it is written RED first,
  against the exact fixture in §1.
- **DAC-35** — `tool_result` with string content, with a block list, nested one
  level, empty, and malformed each extract without raising; unknown block types
  are skipped and counted.
- **DAC-36** — bounds hold: a synthetic 300-message run with 1 MB tool outputs
  stays inside a stated extraction budget, and truncation is recorded.
- **DAC-37** — `extract_direct_input`'s role restriction is unchanged by the
  refactor, asserted directly so a later edit cannot quietly widen Phase A.
- **DAC-38** — with nested redaction in observe mode, a `tool_result` containing
  an email and a card produces a would-redact count and an unmodified request;
  in enforce mode the same request egresses redacted with pairing intact and
  arguments still valid JSON.
- **DAC-39** — no block content appears in logs, metrics labels, counters, or
  test evidence.

## 5. Rollout

| Slice | Content | Gate |
|---|---|---|
| A | Verify the blast radius (§6 item 1) | none — investigation only |
| B | Read coverage: DFR-41…44, DAC-34…37 | architecture review |
| C | Write coverage, observe mode: DFR-45…47, DAC-38…39 | security review + HITL |
| D | Promotion to enforce | measurement window + explicit human decision |

Slice B is independently valuable and independently shippable: it closes the
keyword and threat-detection blindness and fixes the router's input, without
changing a single outbound byte.

## 6. Open questions

1. **Blast radius.** Does the gap extend to `/v1/responses`, the MCP path, and
   the JSONL / FathomDB recorders? Answer before any severity claim is published.
2. **Disclosure.** A shipped release redacts less than the README states on a
   documented path. Whether that warrants a security advisory and a
   CHANGELOG-visible note, and whether 0.6.0 is the right vehicle or this
   deserves a patch release, is an owner decision.
3. **Assistant-history redaction.** Redacting `tool_use` input rewrites
   conversation history the client already acted on. Acceptable, or read-only
   coverage for assistant-authored blocks with redaction confined to
   `tool_result`?
4. **Should the guardrails see the raw Anthropic shape at all?** An alternative
   is to normalize once at the guardrail boundary, so every guard sees one
   vocabulary. That is a larger change with its own fidelity risk, and it is not
   proposed here — but it is the structurally cleaner answer if a third wire
   format arrives.

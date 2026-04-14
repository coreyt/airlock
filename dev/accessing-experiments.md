# Accessing Experiments via Airlock

**Date:** April 11, 2026  
**Context:** Airlock Proxy / Prompt Engineering & A/B Testing

## The Challenge
Organizations frequently need to test "experiments" on LLMs (e.g., injecting a "Caveman" system prompt to reduce token costs by 75%, or a "Deep Thinker" prompt to improve accuracy). 

When deploying these experiments across a fleet of developers using various AI coding tools (Cursor, GitHub Copilot, Claude Code), the proxy must provide a way for users to "opt-in" to an experiment—or stack multiple experiments together—without requiring changes to the closed-source IDEs they use.

This document outlines four architectural approaches for exposing and stacking these experiments through the Airlock proxy.

---

## Option A: HTTP Header Routing (`X-Airlock-Experiment: caveman`)

The traditional web-app approach to feature flagging. The client injects a custom HTTP header, and an Airlock `pre_call` Guardrail intercepts it to apply the prompt/parameter mutations before routing to the underlying model.

*   **How it stacks:** The user sends a comma-separated list: `X-Airlock-Experiment: caveman, thinker`. Airlock concatenates the system prompts and merges the parameters.
*   **Pros:** Clean separation of concerns (headers vs. payload). Standard HTTP practice.
*   **Cons (Fatal for IDEs):** Closed-source tools like Cursor and Copilot do not allow users to inject custom HTTP headers. They only expose a `Base URL` and `API Key` field. This approach restricts experimentation exclusively to developers writing custom Python/curl scripts.

---

## Option B: Logical Model Names (The Hardcoded Approach)

Experiments are defined as virtual, 1:1 mapped model names in `config.yaml`. To the developer, the experiment simply looks like a new model in their IDE's dropdown menu.

*   **How it works:**
    ```yaml
    model_list:
      - model_name: exp/caveman-gemma
        litellm_params:
          model: exp/caveman-gemma
          experiment_profile:
            target_model: local/gemma-4
            system_prompt: "You are Caveman. Use fragments. No filler."
    ```
*   **How it stacks:** It doesn't scale. You must hardcode every possible permutation in the YAML file (e.g., `exp/caveman-thinker-gemma`, `exp/thinker-caveman-gemma`).
*   **Pros:** Universally compatible with all AI tools. Excellent visibility in JSONL logs and SQL databases (the experiment is explicitly recorded as the `model` used).
*   **Cons:** "Combinatorial explosion." Adding 5 experiments across 3 base models requires dozens of lines of redundant YAML configuration.

---

## Option C: Model Name DSL (The "Suffix" Approach) 🏆 *Recommended*

Instead of hardcoding every combination, Airlock’s Interceptor is taught to parse dynamic model strings using a delimiter (e.g., `+` or `@`). The developer types the stacked configuration directly into the "Model Name" field of their IDE.

*   **How it works:**
    1.  The admin defines a registry of "Experiment Fragments" in `config.yaml` (independent of base models).
    2.  The developer types: `local/gemma-4+caveman+thinker`.
    3.  Airlock splits the string, resolves `local/gemma-4` as the physical target, and dynamically injects the `caveman` and `thinker` profiles into the payload.
*   **How it stacks:** Unlimited, dynamic stacking determined entirely by the client string.
*   **Conflict Resolution:** If `caveman` forces `temperature: 0.1` and `thinker` forces `temperature: 0.8`, Airlock applies a strict "Last-One-Wins" rule (based on string order) or raises an HTTP 400 error for explicit conflicts.
*   **Pros:** 100% compatible with all AI tools. Eliminates YAML bloat. Perfectly records the exact stack used in the analytics logs (`model: local/gemma-4+caveman+thinker`).

---

## Option D: "Magic Tags" (In-Band Prompt Routing)

If an AI tool enforces a strict dropdown for model selection and prevents typing custom model strings, experiments can be triggered via "Magic Tags" inside the user's actual chat message.

*   **How it works:**
    1.  The developer types: `[/exp: caveman, thinker] Why is my React component crashing?`
    2.  Airlock’s `pre_call` guardrail uses regex to scan the incoming `messages` array for the tag.
    3.  Airlock dynamically applies the `caveman` and `thinker` profiles to the payload.
    4.  **Crucially:** Airlock *deletes* the `[/exp: ...]` tag from the message before sending it to the upstream LLM, preventing the model from getting confused by proxy routing instructions.
*   **How it stacks:** The user lists multiple experiments inside the brackets.
*   **Pros:** 100% client-agnostic. Allows developers to toggle experiments on a *per-message* basis rather than a per-session basis.
*   **Cons:** "Pollutes" the user's chat interface. Requires the developer to remember the specific syntax for the magic tags. Less structured than Option C for analytics grouping.
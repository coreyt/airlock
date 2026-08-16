# FIX-1 / FIX-2 — Admin-default and include-policy review disposition

**Status:** reviewed; no implementation applies to the current 0.5.15 tree.

## Finding confirmation

The two P1 findings are valid for the reviewed patch description:

* **FIX-1:** an active root `admin.enabled: true` changes the shipped default,
  mounts the Admin control plane, and invokes CC-12 on external plaintext binds.
* **FIX-2:** a recursively resolving child policy loader disagrees with pinned
  LiteLLM's one-level `ProxyConfig._process_includes` behavior and could derive
  an enabled Admin policy where LiteLLM's direct include resolution leaves it
  disabled.

Neither change is present here. `config.yaml` retains its commented Admin sample
with `enabled: false`; parsing it yields no `admin` mapping and
`configure_admin()` defaults to disabled. `airlock/config_loader.py` is absent.
The existing recursive `_inline_config_includes()` is used only when the MCP-off
runtime config is materialized and is handed to LiteLLM; it must not be reused
as an independent Admin-policy resolver.

## Solution hypothesis

If the reviewed patch is introduced or reintroduced:

1. **FIX-1:** remove the active root `admin` mapping and retain the commented,
   opt-in sample. Do not replace it with any enabled default.
2. **FIX-2:** remove the independent recursive child-policy resolver. Either
   leave current startup ownership unchanged, or use a single resolver whose
   output is the exact mapping/file consumed by both LiteLLM and every Airlock
   policy consumer. A limited resolver must mirror LiteLLM's direct include
   overlay exactly; it must not recurse.

Pseudo-code for the safe limited alternative:

```text
effective = deep_copy(root)
for direct_path in effective.include:  # list only, in listed order
    included = parse_yaml(direct_path)
    for key, value in included.items():
        if is_list(value) and key exists in effective:
            effective[key].extend(value)
        else:
            effective[key] = value
delete effective.include
configure_all_startup_consumers(effective)
launch_litellm_with_file_serialized_from(effective)
```

This is only safe when both consumers receive `effective`, and when LiteLLM's
relative-path and error behavior are preserved. A recursive variant must first
materialize the recursive result and pass that same materialized result to
LiteLLM; configuring only Admin from it is rejected.

## Blast radius and implications

Restoring the commented default is a safe rollback for every default deployment:
it preserves Admin 404s and avoids unexpected CC-12 startup failures. Removing
the divergent loader prevents an authorization split-brain. Expanding include
resolution to other startup consumers is a broader configuration-semantics
change, potentially changing aliases, settings, and guardrails; it is out of
scope without a dedicated design and ownership decision.

## Required verification if the reviewed patch exists

1. **RED then GREEN:** a root-template test that parses `config.yaml`, proves
   `admin` is absent (or disabled), configures policy, and observes 404 on an
   Admin route. Verify an external host with that default does not trigger
   CC-12.
2. **RED then GREEN:** fixture root → direct include (`admin.enabled: false`) →
   nested include (`admin.enabled: true`). Assert Airlock policy equals
   LiteLLM's effective direct-resolution result: disabled. Cover direct true,
   direct include order, dict/scalar top-level replacement, list extension, and
   a nested include being ignored by the one-level resolver.
3. Assert LiteLLM receives the same resolved mapping/file used for policy, if
   resolution ownership changes.

Current tests already exercise Admin disabled/configured policy and runtime
include materialization. No new RED test exists in this tree because the alleged
behavior is absent; adding a passing-only test would not satisfy the requested
TDD evidence. The existing `config.yaml` and Admin-guide off-by-default wording
remain correct, so no product-documentation edit is warranted. This disposition
is the durable documentation update for the review result.

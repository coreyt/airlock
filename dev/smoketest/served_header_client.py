#!/usr/bin/env python3
"""Reusable smoke-test client for Airlock's served-backend response headers.

This is a dependency-light client (stdlib ``urllib`` only) that talks to a
RUNNING Airlock proxy as if it were a real OpenAI-compatible client. It is
designed for verifying the 0.5.0 transparency headers end-to-end:

    * ``X-Airlock-Served-By``     — the provider that actually served the call
    * ``X-Airlock-Served-Region`` — the served region (gateway backends only)
    * ``X-Airlock-Mutations``     — compact ledger of request mutations

It captures and prints the HTTP status, the FULL response header set, and (for
non-streaming) the parsed JSON body including the additive ``airlock`` envelope
that the proxy attaches when ``X-Airlock-Explain: 1`` is sent.

SAFETY
------
This module makes a network call ONLY to the ``--base-url`` you pass and ONLY
when you run it. Importing it performs no I/O. Point it at an ISOLATED test
instance (see ``run_isolated_instance.sh``), never at production (port 4000 /
8090). It never targets ``/health`` — only ``/health/liveliness`` — because
``/health`` fans out live completions to every configured model.

Usage
-----
As a script::

    python dev/smoketest/served_header_client.py \
        --base-url http://127.0.0.1:4137 \
        --model gemini-3.5-flash-aistudio \
        --api-key "$AIRLOCK_MASTER_KEY" \
        [--prompt "ping"] [--stream] [--explain] [--health] [--json]

As a library::

    from dev.smoketest.served_header_client import chat_completion, SmokeResult
    result = chat_completion(base_url, api_key, "gemini-3.5-flash-vertex", "ping")
    print(result.served_by, result.served_region)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Response headers we care about most; printed first in the summary. The capture
# itself is exhaustive — every header is retained in ``SmokeResult.headers``.
AIRLOCK_HEADERS = (
    "X-Airlock-Served-By",
    "X-Airlock-Served-Region",
    "X-Airlock-Mutations",
)

# Opt-in header that asks the proxy to attach the additive ``airlock`` body
# envelope on non-streaming responses (mutation ledger). Mirrors the proxy
# default ``explain_body_optin_header`` in airlock/transparency.py.
EXPLAIN_HEADER = "X-Airlock-Explain"

DEFAULT_PROMPT = "Reply with the single word: pong."
DEFAULT_TIMEOUT = 60


@dataclass
class SmokeResult:
    """Captured outcome of a single request, for printing or assertions."""

    ok: bool
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body_text: str = ""
    body_json: dict[str, Any] | None = None
    requested_model: str | None = None
    error: str | None = None

    # -- convenience accessors (case-insensitive header lookup) --------------
    def header(self, name: str) -> str | None:
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return None

    @property
    def served_by(self) -> str | None:
        return self.header("X-Airlock-Served-By")

    @property
    def served_region(self) -> str | None:
        return self.header("X-Airlock-Served-Region")

    @property
    def mutations(self) -> str | None:
        return self.header("X-Airlock-Mutations")

    @property
    def served_model(self) -> str | None:
        """Model id reported in the response body (the served model)."""
        if isinstance(self.body_json, dict):
            return self.body_json.get("model")
        return None

    @property
    def airlock_envelope(self) -> Any | None:
        """The additive ``airlock`` body envelope (present only with --explain)."""
        if isinstance(self.body_json, dict):
            return self.body_json.get("airlock")
        return None


def _auth_headers(api_key: str | None, client: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if client:
        headers["X-Airlock-Client"] = client
    return headers


def _do_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout: int,
    stream: bool,
) -> SmokeResult:
    """Issue one HTTP request and capture status + all headers + body.

    Returns a :class:`SmokeResult` even on HTTP error statuses (4xx/5xx are
    captured, not raised) so the caller can inspect headers regardless.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.status
            resp_headers = {k: v for k, v in resp.headers.items()}
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # HTTP error: still capture status + headers + body for diagnostics.
        resp_headers = {k: v for k, v in (exc.headers or {}).items()}
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return SmokeResult(
            ok=False,
            status=exc.code,
            headers=resp_headers,
            body_text=body,
            error=f"HTTP {exc.code} {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return SmokeResult(ok=False, status=0, error=f"connection failed: {exc.reason}")

    body_text = raw.decode("utf-8", "replace")
    body_json: dict[str, Any] | None = None
    if not stream:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                body_json = parsed
        except json.JSONDecodeError:
            body_json = None

    return SmokeResult(
        ok=200 <= status < 300,
        status=status,
        headers=resp_headers,
        body_text=body_text,
        body_json=body_json,
    )


def chat_completion(
    base_url: str,
    api_key: str | None,
    model: str,
    prompt: str = DEFAULT_PROMPT,
    *,
    stream: bool = False,
    explain: bool = False,
    client: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_tokens: int = 16,
) -> SmokeResult:
    """POST an OpenAI-compatible /chat/completions request and capture headers.

    Parameters
    ----------
    base_url:
        Proxy base URL, e.g. ``http://127.0.0.1:4137`` (no trailing path).
    api_key:
        Bearer token (the test instance ``AIRLOCK_MASTER_KEY``). May be ``None``.
    model:
        The Airlock model alias to request (e.g. ``gemini-3.5-flash-vertex``).
    prompt:
        Short user prompt — keep it tiny to minimize token spend.
    stream:
        When ``True`` send ``stream=true`` and read the SSE body as text.
    explain:
        When ``True`` send ``X-Airlock-Explain: 1`` to opt into the additive
        ``airlock`` body envelope (non-streaming only, per proxy design).
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = _auth_headers(api_key, client)
    if explain:
        headers[EXPLAIN_HEADER] = "1"

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": stream,
    }

    result = _do_request(
        url,
        method="POST",
        headers=headers,
        payload=payload,
        timeout=timeout,
        stream=stream,
    )
    result.requested_model = model
    return result


def health_liveliness(
    base_url: str,
    api_key: str | None,
    *,
    client: str = "smoketest",
    timeout: int = 10,
) -> SmokeResult:
    """GET /health/liveliness — a cheap reachability probe.

    NEVER use /health here: that endpoint fires live completions against every
    configured model. /health/liveliness is the lightweight liveness check.
    """
    url = base_url.rstrip("/") + f"/health/liveliness?client={client}"
    headers = _auth_headers(api_key, client)
    headers.pop("Content-Type", None)
    return _do_request(
        url,
        method="GET",
        headers=headers,
        payload=None,
        timeout=timeout,
        stream=False,
    )


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------
def _print_summary(result: SmokeResult, *, stream: bool, explain: bool) -> None:
    print("=" * 70)
    print(f"HTTP status        : {result.status}  ({'ok' if result.ok else 'NOT ok'})")
    if result.error:
        print(f"error              : {result.error}")
    print(f"requested model    : {result.requested_model}")
    print(f"served model (body): {result.served_model}")
    print("-" * 70)
    print("Airlock headers (the point of this test):")
    for name in AIRLOCK_HEADERS:
        value = result.header(name)
        marker = "" if value is not None else "   (absent)"
        print(f"  {name:24s}: {value if value is not None else ''}{marker}")
    print("-" * 70)
    print("ALL response headers:")
    for key in sorted(result.headers):
        print(f"  {key}: {result.headers[key]}")
    print("-" * 70)
    if stream:
        print("Streaming body (raw SSE, first 800 chars):")
        print(result.body_text[:800])
    else:
        if result.airlock_envelope is not None:
            print("airlock envelope (from --explain):")
            print(json.dumps(result.airlock_envelope, indent=2))
        elif explain:
            print("airlock envelope: (none — no mutations recorded for this call)")
        # Show a trimmed body so the operator can eyeball the completion.
        snippet = result.body_text[:600]
        print("Body (first 600 chars):")
        print(snippet)
    print("=" * 70)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke-test Airlock served-backend response headers.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AIRLOCK_SMOKE_BASE_URL", "http://127.0.0.1:4137"),
        help="Proxy base URL (default: env AIRLOCK_SMOKE_BASE_URL or "
        "http://127.0.0.1:4137). NEVER point at production (4000/8090).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Airlock model alias to request (required unless --health).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIRLOCK_MASTER_KEY"),
        help="Bearer token (default: env AIRLOCK_MASTER_KEY).",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Short user prompt to send.",
    )
    parser.add_argument(
        "--client",
        default="smoketest",
        help="X-Airlock-Client header value (default: smoketest).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16,
        help="Cap completion tokens to keep the smoke test cheap (default: 16).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Send stream=true and capture the SSE body.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Send X-Airlock-Explain: 1 to request the airlock body envelope.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="GET /health/liveliness instead of a completion (never /health).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of the text report.",
    )
    return parser


def _refuse_production(base_url: str) -> None:
    """Hard guard: refuse to target the known production ports."""
    for bad in (":4000", ":8090"):
        if bad in base_url:
            print(
                f"REFUSING: base-url {base_url!r} targets a production port ({bad}). "
                "Use an isolated test instance (see run_isolated_instance.sh).",
                file=sys.stderr,
            )
            raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _refuse_production(args.base_url)

    if args.health:
        result = health_liveliness(
            args.base_url, args.api_key, client=args.client, timeout=args.timeout
        )
        if args.json:
            print(json.dumps({"status": result.status, "ok": result.ok,
                              "headers": result.headers, "error": result.error}))
        else:
            _print_summary(result, stream=False, explain=False)
        return 0 if result.ok else 1

    if not args.model:
        print("error: --model is required (or pass --health).", file=sys.stderr)
        return 2

    result = chat_completion(
        args.base_url,
        args.api_key,
        args.model,
        args.prompt,
        stream=args.stream,
        explain=args.explain,
        client=args.client,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
    )

    if args.json:
        print(json.dumps({
            "status": result.status,
            "ok": result.ok,
            "requested_model": result.requested_model,
            "served_model": result.served_model,
            "served_by": result.served_by,
            "served_region": result.served_region,
            "mutations": result.mutations,
            "headers": result.headers,
            "airlock_envelope": result.airlock_envelope,
            "error": result.error,
        }, indent=2))
    else:
        _print_summary(result, stream=args.stream, explain=args.explain)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

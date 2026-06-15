"""Async content-scan pipeline for batch uploads (design §3.2/§4, to-do #2).

Closes the batch guardrail-bypass gap: every uploaded JSONL row is streamed
(never fully buffered), keyword-checked (a hit rejects the **whole** upload —
bulk blast radius), and PII-redacted (**terminal** redaction; no reverse map is
persisted, per design A2). The provider job is created only after a clean scan.

The pipeline core (``scan_stream``) is **pure** and guard-injected, so it is
fully unit-testable without Presidio/spaCy or the network. ``scan_file`` is the
IO wrapper the async worker (``airlock.batch.worker``) drives in a thread pool.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

logger = logging.getLogger("airlock.batch")

# Guard injection points (real adapters below; fakes in tests).
#   KeywordCheck(text) -> the offending keyword, or None if clean.
#   RedactMessages(messages) -> a scrubbed copy of the messages list.
KeywordCheck = Callable[[str], str | None]
RedactMessages = Callable[[list[dict]], list[dict]]

READY = "ready"
REJECTED = "rejected"


class ContentRejected(Exception):
    """A scan rejected the upload (keyword hit or a cap was exceeded).

    Rejection is upload-wide on purpose (design §4): a single blocked row taints
    the whole batch, so we never ship a partially-clean file to the provider.
    """


@dataclass
class ScanResult:
    status: str  # READY | REJECTED
    reason: str | None = None
    row_count: int = 0
    byte_count: int = 0


# ---------------------------------------------------------------------------
# Pure core — stream rows, scan, yield scrubbed lines (no IO, no guards baked in)
# ---------------------------------------------------------------------------
def _line_text(body: dict) -> str:
    """Concatenate the scannable text of one OpenAI batch request line.

    Mirrors the chat path's ``extract_text``: only message ``content`` is
    user-authored prompt text. Non-string parts contribute their ``text`` field.
    """
    parts: list[str] = []
    for msg in body.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    return "\n".join(parts)


def scan_stream(
    lines: Iterable[str],
    *,
    keyword_check: KeywordCheck | None,
    redact_messages: RedactMessages | None,
    max_rows: int,
    max_bytes: int,
) -> Iterator[str]:
    """Yield scrubbed JSONL lines for a clean upload; raise on rejection.

    Streams ``lines`` one at a time so peak memory is a single row, never the
    whole (≤2 GB) upload. Non-JSON lines (e.g. multipart boundaries) are skipped,
    matching the gateway's own ``_iter_input_lines`` tolerance. Caps are enforced
    on the *processed* content; exceeding either raises :class:`ContentRejected`.
    """
    row_count = 0
    byte_count = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            line = json.loads(stripped)
        except json.JSONDecodeError:
            continue  # boundary / non-JSON noise — never shipped

        byte_count += len(raw.encode("utf-8"))
        if byte_count > max_bytes:
            raise ContentRejected(
                f"upload exceeds max_bytes ({max_bytes}); scan aborted"
            )

        body = line.get("body") if isinstance(line.get("body"), dict) else {}
        text = _line_text(body)

        if keyword_check is not None and text:
            hit = keyword_check(text)
            if hit:
                raise ContentRejected(f"blocked keyword detected: {hit!r}")

        if redact_messages is not None and body.get("messages"):
            body["messages"] = redact_messages(body["messages"])
            line["body"] = body

        row_count += 1
        if row_count > max_rows:
            raise ContentRejected(f"upload exceeds max_rows ({max_rows}); scan aborted")

        yield json.dumps(line)


# ---------------------------------------------------------------------------
# Real guard adapters (lazy provider/Presidio import; gated by the profile)
# ---------------------------------------------------------------------------
def _real_keyword_check() -> KeywordCheck | None:
    """Keyword checker bound to the operator-configured block list.

    Returns ``None`` (a no-op) when no keywords are configured, so an empty list
    costs nothing per row.
    """
    from airlock.guardrails.keyword_guard import (  # noqa: PLC0415
        _blocked_keywords,
        _normalize_text,
    )

    keywords = _blocked_keywords()
    if not keywords:
        return None

    def check(text: str) -> str | None:
        norm = _normalize_text(text).lower()
        for kw in keywords:
            if kw in norm:
                return kw
        return None

    return check


def _real_redactor() -> RedactMessages:
    """Terminal-redaction adapter: scrub messages, **discard** the reverse map."""
    from airlock.guardrails.pii_guard import _scrub_messages  # noqa: PLC0415

    def redact(messages: list[dict]) -> list[dict]:
        # Throwaway mapping/counters => nothing persisted (terminal redaction, A2).
        return _scrub_messages(messages, {}, {})

    return redact


def build_guards(
    profile: dict,
) -> tuple[KeywordCheck | None, RedactMessages | None]:
    """Resolve the real guard callables enabled by ``profile`` (design §4).

    The posture is config-controlled (``batch_profile``), never caller-supplied
    (trust boundary). Either guard may be ``None`` when its flag is off.
    """
    keyword_check = _real_keyword_check() if profile.get("keyword_block") else None
    redactor = _real_redactor() if profile.get("pii_redact") else None
    return keyword_check, redactor


# ---------------------------------------------------------------------------
# IO wrapper — the unit the worker runs in a thread pool
# ---------------------------------------------------------------------------
def _iter_file_lines(path: str) -> Iterator[str]:
    with open(path, encoding="utf-8") as f:
        yield from f


def scan_file(
    input_path: str,
    output_path: str,
    profile: dict,
    *,
    guards: tuple[KeywordCheck | None, RedactMessages | None] | None = None,
) -> ScanResult:
    """Scan a stored upload, writing a scrubbed JSONL on success (design §3.2).

    Streams ``input_path`` → ``output_path`` through :func:`scan_stream`. The
    output is written to a temp sibling and atomically renamed, so a crashed
    scan never leaves a half-written "clean" file for ``create`` to ship. On
    rejection the partial output is removed and a ``REJECTED`` result returned.

    ``guards`` is injectable for tests; in production it is resolved from the
    profile via :func:`build_guards` (lazy Presidio import).
    """
    keyword_check, redactor = guards if guards is not None else build_guards(profile)
    max_rows = int(profile.get("max_rows", 50000))
    max_bytes = int(profile.get("max_bytes", 2147483648))

    tmp_path = f"{output_path}.partial"
    row_count = 0
    try:
        with open(tmp_path, "w", encoding="utf-8") as out:
            for line in scan_stream(
                _iter_file_lines(input_path),
                keyword_check=keyword_check,
                redact_messages=redactor,
                max_rows=max_rows,
                max_bytes=max_bytes,
            ):
                out.write(line + "\n")
                row_count += 1
    except ContentRejected as exc:
        _safe_unlink(tmp_path)
        logger.warning("batch upload rejected by scan: %s", exc)
        return ScanResult(status=REJECTED, reason=str(exc))
    except BaseException:
        _safe_unlink(tmp_path)
        raise

    os.replace(tmp_path, output_path)
    return ScanResult(status=READY, row_count=row_count)


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass

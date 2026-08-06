"""Per-client erasure from the FathomDB store (0.5.11 B-2).

``erase_source(client_id)`` removes every row under a provenance together with
its full-text and secondary-index shadows, and finishes the erasure at rest.
It is idempotent, so an interrupted obligation can be retried.

**Scoping — this is not full erasure.** It erases a client from the **search
and analysis store**. The same records exist in JSONL, which ``erase_source``
does not touch; JSONL retention is governed separately by
``AIRLOCK_MAX_LOG_DAYS``. A user-facing deletion obligation requires both, and
the JSONL half is explicitly out of scope for 0.5.11.
"""

from __future__ import annotations

import datetime
from typing import Any


class EraseIncomplete(Exception):
    """An erasure half-completed: the obligation is OUTSTANDING.

    Never reported as success — the honest response is "incomplete, retry".
    Retrying is safe; that is what ``erase_source``'s idempotence buys.
    Carries the ``admin_action`` audit record for the failed attempt.
    """

    def __init__(self, record: dict[str, Any]):
        super().__init__(record.get("error") or "erasure incomplete")
        self.record = record


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _report_dict(report: Any) -> dict[str, Any]:
    """Serialize an ``EraseReport`` — the receipt, not a bare "ok".

    An erasure audit trail that records only that the call was made cannot
    answer what was actually removed.
    """
    return {
        "source_ref": report.source_ref,
        "nodes_excised": report.nodes_excised,
        "edges_excised": report.edges_excised,
        "projections_invalidated": report.projections_invalidated,
    }


def erase_client(client_id: str, actor: str, *, confirm: Any) -> dict[str, Any]:
    """Erase every FathomDB row whose provenance is ``client_id``.

    ``confirm`` must repeat the client id — erasure is irreversible and must
    not be a single mistyped word away.

    Returns an ``admin_action`` record carrying the ``EraseReport``.

    Raises
    ------
    ValueError
        Confirmation mismatch, or the datastore is not enabled here.
    EraseIncomplete
        The erasure half-completed; the record says so and retry is safe.
    """
    if not client_id:
        raise ValueError("client_id is required")
    if confirm != client_id:
        raise ValueError(
            "confirmation mismatch: pass the client id again as 'confirm' "
            "to run this irreversible operation"
        )

    import airlock.datastore as datastore

    engine = datastore.get_engine()
    if engine is None:
        raise ValueError(
            "FathomDB is not enabled on this proxy (AIRLOCK_ENABLE_FATHOMDB "
            "unset or the db extra is not installed); nothing to erase here"
        )

    from fathomdb.errors import ErasureIncompleteError

    base: dict[str, Any] = {
        "record_type": "admin_action",
        "timestamp": _iso_now(),
        "op": "erase_client",
        "actor": actor,
        "client_id": client_id,
        # The scoping constraint, carried into the audit trail itself.
        "scope_note": "fathomdb store only; JSONL retention is governed by AIRLOCK_MAX_LOG_DAYS",
    }
    try:
        report = engine.erase_source(client_id)
    except ErasureIncompleteError as exc:
        raise EraseIncomplete(
            {
                **base,
                "outcome": "incomplete",
                "error": str(exc),
                "retry_safe": True,
            }
        ) from exc
    return {**base, "outcome": "complete", "erase_report": _report_dict(report)}

import json
from datetime import datetime, timezone
from typing import Any


def node_properties(node: Any) -> dict[str, Any]:
    """Return a NodeRecord's body as a properties dict.

    FathomDB 0.8.x returns canonical rows as ``NodeRecord`` with a JSON
    ``body``; 0.3.x's ``.properties`` attribute is gone.
    """
    body = json.loads(node.body)
    return body if isinstance(body, dict) else {}


def get_request_logs(engine, limit: int = 1000000):
    """Retrieve active RequestLog rows from FathomDB.

    Errors surface as the typed ``fathomdb.errors`` hierarchy — there is no
    capability sniffing and no silent empty-list fallback.
    """
    from fathomdb import read

    return read.list(engine, "RequestLog", limit=limit)


def get_billing_metrics(engine):
    nodes = get_request_logs(engine, limit=1000000)

    now = datetime.now(timezone.utc)
    mtd_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    ytd_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)

    mtd_cost = 0.0
    ytd_cost = 0.0

    for node in nodes:
        properties = node_properties(node)
        cost = properties.get("cost", 0.0)
        ts_str = properties.get("timestamp")
        if not ts_str:
            continue

        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if ts >= ytd_start:
            ytd_cost += cost
        if ts >= mtd_start:
            mtd_cost += cost

    return {"MTD_cost": mtd_cost, "YTD_cost": ytd_cost}

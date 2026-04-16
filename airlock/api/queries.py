from datetime import datetime, timezone


def get_request_logs(engine, limit: int = 1000000):
    """Retrieve RequestLog nodes from FathomDB."""
    try:
        return engine.nodes("RequestLog").limit(limit).execute().nodes
    except AttributeError:
        if hasattr(engine, "_query_nodes"):
            return engine._query_nodes("RequestLog", limit=limit)
        return []


def get_billing_metrics(engine):
    # Retrieve all request logs by not limiting to 1000
    nodes = get_request_logs(engine, limit=1000000)

    now = datetime.now(timezone.utc)
    mtd_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    ytd_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)

    mtd_cost = 0.0
    ytd_cost = 0.0

    for node in nodes:
        cost = node.properties.get("cost", 0.0)
        ts_str = node.properties.get("timestamp")
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


def search_logs(engine, query_str: str):
    # Use native FathomDB Full-Text Search
    try:
        nodes = (
            engine.fallback_search(query_str, root_kind="RequestLog").execute().nodes
        )
    except AttributeError:
        # Fallback if engine is mocked or behaves differently
        if hasattr(engine, "_query_nodes"):
            nodes = engine._query_nodes("RequestLog", limit=1000)
            query_str_lower = query_str.lower()
            results = []
            for node in nodes:
                found = False
                for value in node.properties.values():
                    if isinstance(value, str) and query_str_lower in value.lower():
                        found = True
                        break
                if found:
                    results.append(node)
            return results
        else:
            return []

    return nodes

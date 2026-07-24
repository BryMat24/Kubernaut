import os
import time
from typing import Annotated

import requests
from fastmcp import FastMCP
import json

mcp = FastMCP("loki-tools")

LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")


def _range_query(logql: str, minutes: int, limit: int) -> list[dict]:
    """
    Run a LogQL range query against Loki's HTTP API and return matching log lines,
    newest first, as plain dicts (timestamp, line text, and stream labels).
    """
    end = time.time()
    start = end - minutes * 60
    response = requests.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": logql,
            "start": str(int(start * 1e9)),
            "end": str(int(end * 1e9)),
            "limit": limit,
            "direction": "backward",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "success":
        raise RuntimeError(f"Loki query failed: {payload.get('error', payload)}")

    lines = []
    for stream in payload["data"]["result"]:
        for ts, line in stream.get("values", []):
            log = json.loads(line)
            truncated_log = {
                key: (val[:100] if isinstance(val, str) else val) 
                for key, val in log.items()
            }
            lines.append(truncated_log)

    lines.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return lines[:limit]


@mcp.tool
def recent_logs(
    app_label: Annotated[str, "Value of the workload's 'app' label (see the Deployment/Pod labels returned by list_resources)."],
    namespace: Annotated[str, "Namespace containing the workload."] = "default",
    lines: Annotated[int, "Maximum number of log lines to return, newest first."] = 100,
) -> list[dict]:
    """
    Retrieve the most recent log lines across all pods of a workload, aggregated by label.

    LogQL: {app="$app_label", namespace="$namespace"}

    Use when: you need a general view of what a workload is currently logging and don't
    already know a specific pod name -- covers every replica at once. If you already know
    the exact pod name and want its logs, use get_pod_logs instead.
    """
    logql = f'{{app="{app_label}", namespace="{namespace}"}}'
    return _range_query(logql, minutes=60, limit=lines)


@mcp.tool
def error_logs(
    app_label: Annotated[str, "Value of the workload's 'app' label (see the Deployment/Pod labels returned by list_resources)."],
    namespace: Annotated[str, "Namespace containing the workload."] = "default",
    minutes: Annotated[int, "How many minutes back to search."] = 15,
) -> list[dict]:
    """
    Retrieve log lines matching error/exception/panic/fatal patterns across all pods of a
    workload, over a recent time window.

    LogQL: {app="$app_label", namespace="$namespace"} |~ "(?i)error|exception"

    Use when: confirming whether a workload is actually logging failures -- e.g. after a
    Prometheus metric (error_rate, latency_p95) shows a symptom but Kubernetes-level evidence
    (Pod status, Service endpoints) looks healthy. This is where the concrete error message or
    stack trace actually lives.
    """
    logql = f'{{app="{app_label}", namespace="{namespace}"}} |~ "(?i)error|exception|panic|fatal"'
    return _range_query(logql, minutes=minutes, limit=200)
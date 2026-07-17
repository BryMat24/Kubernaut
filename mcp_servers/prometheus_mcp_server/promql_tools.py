import os
import time
from typing import Annotated

import requests
from fastmcp import FastMCP

mcp = FastMCP("promql-tools")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def _instant_query(promql: str) -> list[dict]:
    """
    Run a PromQL instant query against Prometheus's HTTP API and return the
    result vector as plain dicts (metric labels + value + timestamp).
    """
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql, "time": time.time()},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload.get('error', payload)}")

    result_type = payload["data"]["result_type"] if "result_type" in payload["data"] else payload["data"].get("resultType")
    results = payload["data"]["result"]

    if result_type == "scalar":
        ts, value = results
        return [{"metric": {}, "timestamp": ts, "value": value}]

    samples = []
    for item in results:
        ts, value = item["value"]
        samples.append({
            "metric": item.get("metric", {}),
            "timestamp": ts,
            "value": value,
        })
    return samples


# ERROR RATE
@mcp.tool
def error_rate(
    app: Annotated[str, "Value of the app label identifying the service."],
) -> list[dict]:
    """
    Retrieve the fraction of HTTP requests returning 5xx errors over the last 5 minutes.

    PromQL: sum(rate(http_requests_total{app="$app", status=~"5.."}[5m])) /
    sum(rate(http_requests_total{app="$app"}[5m]))

    Use when: investigating whether a service is actively failing requests — a value near 0
    means healthy, a value approaching 1 means most/all requests are erroring.
    """
    promql = (
        f'sum(rate(http_requests_total{{container="{app}", status=~"5.."}}[5m])) / '
        f'sum(rate(http_requests_total{{container="{app}"}}[5m]))'
    )
    return _instant_query(promql)


# LATENCY
@mcp.tool
def latency_p95(
    app: Annotated[str, "Value of the app label identifying the service."],
) -> list[dict]:
    """
    Retrieve the 95th-percentile HTTP request latency over the last 5 minutes.

    PromQL: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{app="$app"}[5m])) by (le))

    Use when: confirming whether users are experiencing slow responses, or checking whether a
    recent change regressed latency — this reads the histogram buckets, not an average, so it
    isn't skewed by a handful of fast requests masking a slow tail.
    """
    promql = (
        f'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{container="{app}"}}[5m])) by (le))'
    )
    return _instant_query(promql)


# OOM indicator
@mcp.tool
def oom_indicator(
    pod: Annotated[str, "Pod name prefix to match (e.g. the Deployment name)."],
) -> list[dict]:
    """
    Retrieve each matching Pod's memory usage as a fraction of its configured memory limit.

    PromQL: container_memory_working_set_bytes{pod=~"$pod.*"} / container_spec_memory_limit_bytes{pod=~"$pod.*"}

    Use when: checking whether a Pod is approaching its memory limit and at risk of being
    OOMKilled — a value approaching 1 means the Pod is close to its limit. Pairs with
    oom_killed_pods to confirm whether an OOMKill has already happened.
    """
    promql = (
        f'container_memory_working_set_bytes{{pod=~"{pod}.*"}} / '
        f'container_spec_memory_limit_bytes{{pod=~"{pod}.*"}}'
    )
    return _instant_query(promql)


# POD restart count
@mcp.tool
def pod_restart_count(
    namespace: Annotated[str, "Namespace to inspect."],
) -> list[dict]:
    """
    Retrieve the number of container restarts per Pod in a namespace over the last 15 minutes.

    PromQL: increase(kube_pod_container_status_restarts_total{namespace="$ns"}[15m])

    Use when: identifying which Pods are crash-looping — a non-zero, growing count points at
    an unstable container. Use get_previous_logs (k8s tools) on the affected Pod to see why.
    """
    promql = f'increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[15m])'
    return _instant_query(promql)


# OOM
@mcp.tool
def oom_killed_pods(
    namespace: Annotated[str, "Namespace to inspect."],
) -> list[dict]:
    """
    Retrieve containers in a namespace whose last termination reason was OOMKilled.

    PromQL: kube_pod_container_status_last_terminated_reason{namespace="$ns", reason="OOMKilled"}

    Use when: confirming whether a Pod's restarts were specifically caused by an out-of-memory
    kill, as opposed to a crash, liveness probe failure, or other termination reason.
    """
    promql = f'kube_pod_container_status_last_terminated_reason{{namespace="{namespace}", reason="OOMKilled"}}'
    return _instant_query(promql)
# Loki MCP Server — Design

Date: 2026-07-24

## Goal

Give `DiagnosisAgent` access to application logs across all pods of a workload, aggregated by
label, without needing to know individual pod names up front — the same way `promql_tools.py`
gives it Prometheus metrics today. Implemented as a third MCP server (`mcp_servers/loki_mcp_server/`)
plus a matching MCP client (`mcp_clients/loki_client.py`), wired into `DiagnosisAgent`'s tool
list via `graph/builder.py`.

## Scope

In scope: a new MCP server exposing two narrow, purpose-built log tools backed by Loki's HTTP
API, a matching MCP client module, wiring into `DiagnosisAgent`'s tool list, and Helm chart
support (Deployment+Service, values, `api`'s env var) so a single `helm install`/`upgrade`
deploys it alongside the other three workloads.

Out of scope: no unit tests (matching the existing `promql_tools.py` precedent — this
codebase does not test every MCP tool file, and `k8s_tools.py`'s coverage is the exception, not
the rule); no changes to `k8s_tools.py`'s existing `get_pod_logs`/`get_previous_logs` (those
remain the right tool for "I already know the pod name and want its exact logs"); no raw/generic
LogQL-query tool (matches this codebase's established convention of narrow, docstring-driven
tools over a general-purpose query passthrough); no changes to `DiagnosisAgent`'s `SYSTEM_PROMPT`
(the current prompt's generic `Available tools: {tools}` style relies on each tool's own
docstring for "when to use this," matching how `promql_tools.py`'s tools are already surfaced).

## Architecture

`mcp_servers/loki_mcp_server/` mirrors `mcp_servers/prometheus_mcp_server/` exactly: a FastMCP
app (`server.py`) mounting a tools module (`loki_tools.py`), a `Dockerfile` matching the
existing pattern (`python:3.12-slim`, `EXPOSE 8000`, `ENV PORT=8000`), and a `requirements.txt`
with the same two dependencies (`fastmcp`, `requests`) since it's a pure HTTP client of Loki's
API — no new RBAC or ServiceAccount, exactly like `prometheus_mcp_server`.

Confirmed against the live cluster (`dev-monitoring/loki`, port 3100, shipped via Promtail):
label set is `app`, `namespace`, `pod`, `container`, `component`, `instance`, `job`, `node_name`,
`stream`, `filename` — `app` values already include `frontend`/`backend`/`cache` (the
`test_app/` fixture chain from `CLAUDE.md`), confirming `{app="...", namespace="..."}` is the
right query shape.

### Tools (`loki_tools.py`)

Both call a shared `_range_query(logql, minutes, limit)` helper hitting Loki's
`/loki/api/v1/query_range` endpoint (the range-query analog of `promql_tools.py`'s
`_instant_query` helper against Prometheus's `/api/v1/query`), returning parsed log lines
(timestamp + line text + stream labels) as plain dicts.

- `recent_logs(app, namespace="default", lines=100)` — most recent N log lines across **all**
  pods matching `{app="<app>", namespace="<namespace>"}`, newest first. Use when: you need a
  general view of what a workload is currently logging without already knowing a specific pod
  name — `k8s_tools.py`'s `get_pod_logs` needs one named pod; this covers every replica at once.
- `error_logs(app, namespace="default", minutes=15)` — log lines matching
  `|~ "(?i)error|exception|panic|fatal"` within `{app="<app>", namespace="<namespace>"}` over the
  last N minutes. Use when: confirming whether a workload is actually logging failures (vs. just
  being slow or returning errors detected only via `error_rate` from `promql_tools.py`) — this is
  where the concrete stack trace or error message actually lives.

### Client (`mcp_clients/loki_client.py`)

Mirrors `promql_client.py` exactly:

```python
LOKI_MCP_SERVER_URL = os.getenv("LOKI_MCP_SERVER_URL", "http://localhost:8082/mcp")
```

Port `8082` is a new, distinct default from the other two local MCP server defaults (`8080`,
`8081`) — deliberately chosen to avoid repeating the port-collision bug diagnosed earlier this
session (a stray `kubectl port-forward` on 8080 silently hijacked `k8s_mcp_server`'s traffic).
`mcp_clients/__init__.py` re-exports `get_mcp_tools` as `get_loki_mcp_tools`, matching the
existing `get_k8s_mcp_tools`/`get_promql_mcp_tools` pattern.

### Wiring (`graph/builder.py`)

`init_diagnosis_agent()` gains a third `await get_loki_mcp_tools()` call, appended to the tool
list passed to `DiagnosisAgent`:

```python
async def init_diagnosis_agent() -> DiagnosisAgent:
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    loki_tools = await get_loki_mcp_tools()
    return DiagnosisAgent(
        diagnosis_llm,
        k8s_tools + promql_tools + loki_tools,
        classifier_llm=classifier_llm,
        compactor_llm=compactor_llm,
    )
```

### Helm chart

A fifth workload, `loki-mcp-server` (Deployment + Service, port 8000 internally, `tcpSocket`
probes, no ServiceAccount/RBAC — same shape as `prometheus-mcp-server`'s templates), plus:

- `values.yaml`: new `lokiMcpServer` block (`image.repository: brymat24/loki_mcp`, `tag: latest`,
  `pullPolicy: IfNotPresent`, `replicaCount: 1`, `lokiUrl: http://loki.dev-monitoring.svc.cluster.local:3100`,
  `resources` matching the other three workloads' defaults).
- `api-deployment.yaml`: new `LOKI_MCP_SERVER_URL` env var,
  `http://{{ .Release.Name }}-loki-mcp-server:8000/mcp`, alongside the existing
  `K8S_MCP_SERVER_URL`/`PROMETHEUS_MCP_SERVER_URL` entries.

## Directory layout (new/changed files)

```
mcp_servers/loki_mcp_server/
  loki_tools.py
  server.py
  requirements.txt
  Dockerfile
mcp_clients/
  loki_client.py
  __init__.py            (add get_loki_mcp_tools re-export)
graph/
  builder.py              (wire loki_tools into init_diagnosis_agent)
helm/templates/
  loki-mcp-server-deployment.yaml
  loki-mcp-server-service.yaml
  api-deployment.yaml      (add LOKI_MCP_SERVER_URL env var)
helm/values.yaml           (add lokiMcpServer block)
```

## Testing / validation plan

No unit tests (per decision, matching the `promql_tools.py` precedent). Validation is:

- `python3 -c "import ast; ast.parse(...)"` syntax check on every new/edited Python file.
- The existing `pytest` unit suite must remain unaffected (no existing test imports/exercises
  `loki_tools.py`, `loki_client.py`, or the two edited chart-adjacent Python files beyond
  `graph/builder.py`, which has no existing test coverage of its own to break).
- `helm lint helm` and `helm template kubernaut helm` must pass, with the rendered kind-count
  including the two new resources (Deployment, Service) alongside the existing eleven.
- A manual smoke check against the live cluster's Loki instance (already confirmed reachable and
  correctly labeled during brainstorming) to confirm the LogQL shape actually returns data for at
  least one real `app` value (e.g. `frontend`) before considering the tools done.

## Open questions / assumptions carried forward

None outstanding — tool scope, testing decision, and the Loki label schema were all resolved
during brainstorming against the live cluster, not left as assumptions.

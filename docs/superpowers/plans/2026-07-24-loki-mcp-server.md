# Loki MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third MCP server (Loki-backed logs) and client, give `DiagnosisAgent` access to it, and deploy it via the Helm chart alongside the existing `k8s`/`prometheus` MCP servers.

**Architecture:** `mcp_servers/loki_mcp_server/` mirrors `mcp_servers/prometheus_mcp_server/` exactly (FastMCP app, HTTP client of an existing Loki instance, no RBAC). Two narrow tools (`recent_logs`, `error_logs`) query Loki's `/loki/api/v1/query_range` API by `{app, namespace}` labels. `mcp_clients/loki_client.py` mirrors `promql_client.py`. `graph/builder.py` adds a third tool source to `DiagnosisAgent`. Helm chart gets a fifth workload.

**Tech Stack:** Python 3.12 (container) / 3.11 (repo venv, already has `fastmcp==3.4.4` and `requests==2.34.2` installed — confirmed importable), FastMCP, Helm 3, `kubectl`.

## Global Constraints

- Loki is already deployed in-cluster at `dev-monitoring/loki:3100`, labels confirmed: `app`, `namespace`, `pod`, `container`, `component`, `instance`, `job`, `node_name`, `stream`, `filename` — `app` values include `frontend`/`backend`/`cache`. Query shape is `{app="<app>", namespace="<namespace>"}`.
- Exactly two tools, no more: `recent_logs(app, namespace="default", lines=100)` and `error_logs(app, namespace="default", minutes=15)`. No raw/generic LogQL-query tool.
- No unit tests for this feature (matches the existing `promql_tools.py` precedent — that file has zero test coverage today).
- New MCP server's local dev default port is `8082` (distinct from `k8s_mcp_server`'s `8080` and `prometheus_mcp_server`'s `8081`, to avoid the port-collision failure mode already diagnosed this session). Container port is `8000` (`EXPOSE 8000`/`ENV PORT=8000` in the Dockerfile), matching the other two MCP servers' container convention.
- `mcp_servers/` has no `__init__.py` anywhere and relies on PEP 420 implicit namespace packages (confirmed: `from mcp_servers.k8s_mcp_server import k8s_tools` already works this way) — do not add an `__init__.py` to `mcp_servers/loki_mcp_server/`.
- Helm resources for `loki-mcp-server` must match `prometheus-mcp-server`'s exact shape: `tcpSocket` probes, no ServiceAccount/RBAC, `kubernaut.labels`/`kubernaut.selectorLabels` helpers, `app.kubernetes.io/component: loki-mcp-server`.

---

## Task 1: Loki MCP server (`mcp_servers/loki_mcp_server/`)

**Files:**
- Create: `mcp_servers/loki_mcp_server/loki_tools.py`
- Create: `mcp_servers/loki_mcp_server/server.py`
- Create: `mcp_servers/loki_mcp_server/requirements.txt`
- Create: `mcp_servers/loki_mcp_server/Dockerfile`

**Interfaces:**
- Produces: `mcp_servers.loki_mcp_server.loki_tools.mcp` (a `FastMCP` instance with two tools registered: `recent_logs`, `error_logs`) — Task 2 does not consume this directly (it goes through the MCP client over HTTP), but Task 3's `graph/builder.py` wiring assumes the server, once running, exposes exactly these two tool names to `get_mcp_tools()`.

- [ ] **Step 1: Confirm the file doesn't exist yet (failing check)**

Run: `python3 -c "from mcp_servers.loki_mcp_server import loki_tools"`
Expected: `ModuleNotFoundError: No module named 'mcp_servers.loki_mcp_server'`

- [ ] **Step 2: Create `mcp_servers/loki_mcp_server/loki_tools.py`**

```python
import os
import time
from typing import Annotated

import requests
from fastmcp import FastMCP

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
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            lines.append({"timestamp": ts, "line": line, "labels": labels})

    lines.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return lines[:limit]


@mcp.tool
def recent_logs(
    app: Annotated[str, "Value of the app label identifying the workload."],
    namespace: Annotated[str, "Namespace containing the workload."] = "default",
    lines: Annotated[int, "Maximum number of log lines to return, newest first."] = 100,
) -> list[dict]:
    """
    Retrieve the most recent log lines across all pods of a workload, aggregated by label.

    LogQL: {app="$app", namespace="$namespace"}

    Use when: you need a general view of what a workload is currently logging and don't
    already know a specific pod name -- covers every replica at once. If you already know
    the exact pod name and want its logs, use get_pod_logs instead.
    """
    logql = f'{{app="{app}", namespace="{namespace}"}}'
    return _range_query(logql, minutes=60, limit=lines)


@mcp.tool
def error_logs(
    app: Annotated[str, "Value of the app label identifying the workload."],
    namespace: Annotated[str, "Namespace containing the workload."] = "default",
    minutes: Annotated[int, "How many minutes back to search."] = 15,
) -> list[dict]:
    """
    Retrieve log lines matching error/exception/panic/fatal patterns across all pods of a
    workload, over a recent time window.

    LogQL: {app="$app", namespace="$namespace"} |~ "(?i)error|exception|panic|fatal"

    Use when: confirming whether a workload is actually logging failures -- e.g. after a
    Prometheus metric (error_rate, latency_p95) shows a symptom but Kubernetes-level evidence
    (Pod status, Service endpoints) looks healthy. This is where the concrete error message or
    stack trace actually lives.
    """
    logql = f'{{app="{app}", namespace="{namespace}"}} |~ "(?i)error|exception|panic|fatal"'
    return _range_query(logql, minutes=minutes, limit=200)
```

- [ ] **Step 3: Create `mcp_servers/loki_mcp_server/server.py`**

```python
import os

from fastmcp import FastMCP

from loki_tools import mcp as loki_tools_server

app = FastMCP("loki-mcp-server")
app.mount(loki_tools_server)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8082"))
    app.run(transport="http", host="0.0.0.0", port=port)
```

- [ ] **Step 4: Create `mcp_servers/loki_mcp_server/requirements.txt`**

```
fastmcp==3.4.4
requests==2.34.2
```

- [ ] **Step 5: Create `mcp_servers/loki_mcp_server/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY loki_tools.py server.py ./

# LOKI_URL must point at the in-cluster Loki service (e.g.
# http://loki.dev-monitoring.svc.cluster.local:3100) -- no log shipping or
# storage happens in this image, it only queries an existing Loki instance.
ENV LOKI_URL=http://localhost:3100

EXPOSE 8000
ENV PORT=8000

CMD ["python", "server.py"]
```

- [ ] **Step 6: Verify the module imports and both tools are registered**

Run (from repo root, with the repo venv activated):
```bash
source .venv/bin/activate
python3 -c "
from mcp_servers.loki_mcp_server import loki_tools
names = {t.name for t in loki_tools.mcp._tool_manager._tools.values()} if hasattr(loki_tools.mcp, '_tool_manager') else None
print('module OK')
print('recent_logs' in dir(loki_tools), 'error_logs' in dir(loki_tools))
"
```
Expected: `module OK` then `True True` (both tool functions are importable module attributes; FastMCP's internal registration structure may vary by version, so the important assertion is that both function names exist on the module -- if the `_tool_manager` introspection line errors, ignore it and rely on the `True True` line).

- [ ] **Step 7: Smoke-test against the live cluster's Loki instance**

Run:
```bash
kubectl port-forward -n dev-monitoring svc/loki 3100:3100 --request-timeout=30s > /tmp/loki-pf.log 2>&1 &
PF_PID=$!
sleep 3
LOKI_URL=http://localhost:3100 python3 -c "
import sys
sys.path.insert(0, 'mcp_servers/loki_mcp_server')
import importlib
import loki_tools
importlib.reload(loki_tools)
result = loki_tools.recent_logs.fn('frontend', 'dev', 5)
print('lines returned:', len(result))
assert len(result) > 0, 'expected at least one log line for app=frontend in namespace=dev'
print('OK')
"
kill $PF_PID 2>/dev/null
```
Expected: `lines returned: <N>` where N > 0, then `OK`. (`.fn` accesses the undecorated function directly, since `@mcp.tool` wraps it in a FastMCP `Tool` object — adjust to whatever attribute FastMCP 3.4.4 exposes for the raw callable if `.fn` isn't it; check with `dir(loki_tools.recent_logs)` if this errors.)

- [ ] **Step 8: Commit**

```bash
git add mcp_servers/loki_mcp_server/loki_tools.py mcp_servers/loki_mcp_server/server.py mcp_servers/loki_mcp_server/requirements.txt mcp_servers/loki_mcp_server/Dockerfile
git commit -m "$(cat <<'EOF'
feat: add loki_mcp_server with recent_logs/error_logs tools

EOF
)"
```

---

## Task 2: MCP client (`mcp_clients/loki_client.py`)

**Files:**
- Create: `mcp_clients/loki_client.py`
- Modify: `mcp_clients/__init__.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (talks to the server over HTTP once running, not via Python import).
- Produces: `get_loki_mcp_tools()` (async, returns `list`) — Task 3's `graph/builder.py` imports and calls this exactly like `get_k8s_mcp_tools`/`get_promql_mcp_tools`.

- [ ] **Step 1: Confirm the client doesn't exist yet (failing check)**

Run: `python3 -c "from mcp_clients.loki_client import get_mcp_tools"`
Expected: `ModuleNotFoundError: No module named 'mcp_clients.loki_client'`

- [ ] **Step 2: Create `mcp_clients/loki_client.py`**

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
import os

LOKI_MCP_SERVER_URL = os.getenv("LOKI_MCP_SERVER_URL", "http://localhost:8082/mcp")

def get_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "loki": {
            "url": LOKI_MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    })

async def get_mcp_tools() -> list:
    client = get_mcp_client()
    return await client.get_tools()
```

- [ ] **Step 3: Modify `mcp_clients/__init__.py`**

Current content:
```python
from .k8s_client import get_mcp_tools as get_k8s_mcp_tools
from .promql_client import get_mcp_tools as get_promql_mcp_tools
```

New content:
```python
from .k8s_client import get_mcp_tools as get_k8s_mcp_tools
from .promql_client import get_mcp_tools as get_promql_mcp_tools
from .loki_client import get_mcp_tools as get_loki_mcp_tools
```

- [ ] **Step 4: Verify the client imports correctly and the default URL is right**

Run:
```bash
source .venv/bin/activate
python3 -c "
from mcp_clients import get_loki_mcp_tools
from mcp_clients.loki_client import LOKI_MCP_SERVER_URL
print(LOKI_MCP_SERVER_URL)
print(callable(get_loki_mcp_tools))
"
```
Expected:
```
http://localhost:8082/mcp
True
```

- [ ] **Step 5: Commit**

```bash
git add mcp_clients/loki_client.py mcp_clients/__init__.py
git commit -m "$(cat <<'EOF'
feat: add loki MCP client

EOF
)"
```

---

## Task 3: Wire Loki tools into DiagnosisAgent (`graph/builder.py`)

**Files:**
- Modify: `graph/builder.py`

**Interfaces:**
- Consumes: `get_loki_mcp_tools` from Task 2's `mcp_clients` package.
- Produces: `DiagnosisAgent` instantiated with `k8s_tools + promql_tools + loki_tools` (no change to `DiagnosisAgent`'s own constructor signature — it already accepts `tools: list[BaseTool]`).

- [ ] **Step 1: Confirm the current import list doesn't include the Loki client (failing check)**

Run: `grep -n "get_loki_mcp_tools" graph/builder.py`
Expected: no output (empty grep match, exit code 1)

- [ ] **Step 2: Modify `graph/builder.py`'s import line**

Change:
```python
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
```
to:
```python
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools, get_loki_mcp_tools
```

- [ ] **Step 3: Modify `graph/builder.py`'s `init_diagnosis_agent` function**

Change:
```python
async def init_diagnosis_agent() -> DiagnosisAgent:
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    return DiagnosisAgent(
        diagnosis_llm,
        k8s_tools + promql_tools,
        classifier_llm=classifier_llm,
        compactor_llm=compactor_llm,
    )
```
to:
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

- [ ] **Step 4: Verify the file parses and the new import resolves**

Run:
```bash
source .venv/bin/activate
python3 -c "import ast; ast.parse(open('graph/builder.py').read())" && echo "PARSE_OK"
python3 -c "from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools, get_loki_mcp_tools; print('IMPORT_OK')"
grep -n "loki_tools" graph/builder.py
```
Expected: `PARSE_OK`, then `IMPORT_OK`, then two matching lines showing `loki_tools = await get_loki_mcp_tools()` and `k8s_tools + promql_tools + loki_tools`.

- [ ] **Step 5: Confirm the existing unit test suite is unaffected**

Run: `pytest -q 2>&1 | tail -5`
Expected: same pass count as before this task (158 passed, 10 deselected — no existing test imports `graph/builder.py`'s module-level LLM instantiation, so this file has no direct test coverage to break, but the full suite must still show zero regressions).

- [ ] **Step 6: Commit**

```bash
git add graph/builder.py
git commit -m "$(cat <<'EOF'
feat: wire loki tools into DiagnosisAgent

EOF
)"
```

---

## Task 4: Helm chart — `loki-mcp-server` workload

**Files:**
- Create: `helm/templates/loki-mcp-server-deployment.yaml`
- Create: `helm/templates/loki-mcp-server-service.yaml`
- Modify: `helm/values.yaml`
- Modify: `helm/templates/api-deployment.yaml`

**Interfaces:**
- Consumes: `kubernaut.labels`/`kubernaut.selectorLabels` from `helm/templates/_helpers.tpl` (already exists); `.Values.lokiMcpServer.*` (this task adds these keys to `values.yaml` itself, in the same task).
- Produces: Service `{{ .Release.Name }}-loki-mcp-server` on port 8000 -- consumed by this same task's `api-deployment.yaml` edit (`LOKI_MCP_SERVER_URL`).

- [ ] **Step 1: Confirm the chart doesn't have this workload yet (failing check)**

Run: `helm template kubernaut helm -s templates/loki-mcp-server-deployment.yaml`
Expected: `Error: could not find template templates/loki-mcp-server-deployment.yaml in chart`

- [ ] **Step 2: Add `lokiMcpServer` block to `helm/values.yaml`**

Append (after the existing `prometheusMcpServer:` block, matching its exact structure):
```yaml
lokiMcpServer:
    image:
        repository: brymat24/loki_mcp
        tag: latest
        pullPolicy: IfNotPresent
    replicaCount: 1
    lokiUrl: http://loki.dev-monitoring.svc.cluster.local:3100
    resources:
        requests:
            cpu: 100m
            memory: 128Mi
        limits:
            cpu: 500m
            memory: 512Mi
```

- [ ] **Step 3: Create `helm/templates/loki-mcp-server-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-loki-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: loki-mcp-server
spec:
  replicas: {{ .Values.lokiMcpServer.replicaCount }}
  selector:
    matchLabels:
      {{- include "kubernaut.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: loki-mcp-server
  template:
    metadata:
      labels:
        {{- include "kubernaut.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: loki-mcp-server
    spec:
      containers:
        - name: loki-mcp-server
          image: "{{ .Values.lokiMcpServer.image.repository }}:{{ .Values.lokiMcpServer.image.tag }}"
          imagePullPolicy: {{ .Values.lokiMcpServer.image.pullPolicy }}
          ports:
            - containerPort: 8000
          env:
            - name: LOKI_URL
              value: {{ .Values.lokiMcpServer.lokiUrl | quote }}
          readinessProbe:
            tcpSocket:
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 20
          resources:
            {{- toYaml .Values.lokiMcpServer.resources | nindent 12 }}
```

- [ ] **Step 4: Create `helm/templates/loki-mcp-server-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-loki-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: loki-mcp-server
spec:
  type: ClusterIP
  selector:
    {{- include "kubernaut.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: loki-mcp-server
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 5: Add `LOKI_MCP_SERVER_URL` env var to `helm/templates/api-deployment.yaml`**

Find the existing `env:` block (contains `K8S_MCP_SERVER_URL` and `PROMETHEUS_MCP_SERVER_URL`) and add a third entry:
```yaml
            - name: LOKI_MCP_SERVER_URL
              value: http://{{ .Release.Name }}-loki-mcp-server:8000/mcp
```
placed alongside the existing two `- name: ...` entries in that same `env:` list (read the file first to match its current exact indentation before editing, since this file has been restructured earlier in the session).

- [ ] **Step 6: Verify the chart lints, renders, and the new resource appears**

Run:
```bash
helm lint helm
helm template kubernaut helm | grep -E '^kind: ' | sort | uniq -c
helm template kubernaut helm -s templates/api-deployment.yaml | grep -A1 "LOKI_MCP_SERVER_URL"
```
Expected: `1 chart(s) linted, 0 chart(s) failed`; kind counts now show `4 kind: Deployment` → `5 kind: Deployment` and `4 kind: Service` → `5 kind: Service` (ClusterRole/ClusterRoleBinding/ServiceAccount counts unchanged at 1 each); the `api-deployment.yaml` render shows:
```
            - name: LOKI_MCP_SERVER_URL
              value: http://kubernaut-loki-mcp-server:8000/mcp
```

- [ ] **Step 7: Commit**

```bash
git add helm/values.yaml helm/templates/loki-mcp-server-deployment.yaml helm/templates/loki-mcp-server-service.yaml helm/templates/api-deployment.yaml
git commit -m "$(cat <<'EOF'
helm: add loki-mcp-server workload and wire into api

EOF
)"
```

---

## Task 5: Final validation

**Files:** none (validation only).

**Interfaces:** none.

- [ ] **Step 1: Full syntax check on every new/edited Python file**

Run:
```bash
source .venv/bin/activate
for f in mcp_servers/loki_mcp_server/loki_tools.py mcp_servers/loki_mcp_server/server.py mcp_clients/loki_client.py mcp_clients/__init__.py graph/builder.py; do
  python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK: $f"
done
```
Expected: `OK: <path>` for all five files.

- [ ] **Step 2: Full unit test suite**

Run: `pytest -q 2>&1 | tail -5`
Expected: `158 passed, 10 deselected` (same count as before this plan -- this plan adds no test files, per the Global Constraints decision to skip tests for this feature, and touches no code any existing test covers).

- [ ] **Step 3: Full chart validation**

Run:
```bash
helm lint helm
helm template kubernaut helm | grep -E '^kind: ' | sort | uniq -c
```
Expected: `1 chart(s) linted, 0 chart(s) failed`; kind counts:
```
      1 kind: ClusterRole
      1 kind: ClusterRoleBinding
      5 kind: Deployment
      5 kind: Service
      1 kind: ServiceAccount
```

- [ ] **Step 4: End-to-end live check against the actual Loki instance (repeat of Task 1's smoke test, now via the full import path used by the running server)**

Run:
```bash
kubectl port-forward -n dev-monitoring svc/loki 3100:3100 --request-timeout=30s > /tmp/loki-pf.log 2>&1 &
PF_PID=$!
sleep 3
LOKI_URL=http://localhost:3100 python3 -c "
from mcp_servers.loki_mcp_server import loki_tools
result = loki_tools.recent_logs.fn('frontend', 'dev', 5)
print('recent_logs:', len(result), 'lines')
result2 = loki_tools.error_logs.fn('backend', 'dev', 60)
print('error_logs:', len(result2), 'lines')
"
kill $PF_PID 2>/dev/null
```
Expected: `recent_logs: <N> lines` with N > 0; `error_logs: <M> lines` with M >= 0 (zero is a valid outcome if `backend` genuinely logged no errors in the last 60 minutes -- the check is that the call succeeds without raising, not that M > 0).

- [ ] **Step 5: Report final state**

No commit for this task (validation only) -- if any step above fails, fix the specific file it points at and re-run that step, not the whole task list.

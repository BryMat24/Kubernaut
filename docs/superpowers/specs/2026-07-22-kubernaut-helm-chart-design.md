# Kubernaut Helm Chart — Design

Date: 2026-07-22

## Goal

Package the four Kubernaut workloads (`api`, `ui`, `mcp_servers/k8s_mcp_server`,
`mcp_servers/prometheus_mcp_server`) as deployable Kubernetes manifests, then package those
manifests into a Helm chart. Deliverable is committed under `helm/`.

## Scope

In scope: Deployment + Service (+ ServiceAccount/RBAC where needed) for each of the 4
workloads, wired together with correct env vars/ports per their existing Dockerfiles, plus a
Helm chart templating all of it.

Out of scope (explicitly deferred, not addressed by this chart):
- Postgres itself (the chart assumes an external/existing Postgres reachable via
  `DATABASE_URL`; it does not deploy one).
- Ingress / external exposure (ClusterIP only for now).
- CI/CD image build+push pipeline (image `repository`/`tag` are chart values, not resolved here).
- Any change to application code, RBAC verbs granted beyond what `k8s_tools.py` actually
  shells out to today, or to the existing Dockerfiles.

## Architecture

One umbrella chart, `helm/kubernaut/`, with one Deployment+Service pair per workload. Raw,
non-templated Kubernetes manifests are also committed under `helm/manifests/` (one YAML file
per resource) as a plain `kubectl apply -f` reviewable reference — the chart in
`helm/kubernaut/templates/` is the templated version of the same resources, parameterized via
`values.yaml`.

| Workload | Image (repository:tag) | Container port | ServiceAccount / RBAC |
|---|---|---|---|
| `api` | `kubernaut-api:latest` | 8000 | default ServiceAccount, no cluster RBAC — talks to the two MCP servers over HTTP (`K8S_MCP_SERVER_URL`, `PROMETHEUS_MCP_SERVER_URL`), never shells out to `kubectl` itself |
| `ui` | `kubernaut-ui:latest` | 3000 | default ServiceAccount |
| `k8s-mcp-server` | `kubernaut-k8s-mcp-server:latest` | 8000 | dedicated ServiceAccount + read-only ClusterRole (see below) |
| `prometheus-mcp-server` | `kubernaut-prometheus-mcp-server:latest` | 8000 | default ServiceAccount, no k8s RBAC — only calls the Prometheus HTTP API via `PROMETHEUS_URL` |

### Why `k8s-mcp-server` gets a dedicated ClusterRole

Per `CLAUDE.md`, the MCP server and the agent process are meant to run as separate
pods/processes with scoped RBAC — this chart is where that scoping is actually enforced.
Every `kubectl` subcommand `k8s_tools.py` shells out to is read-only (`get`, `describe`,
`logs`, `top`, `rollout status --watch=false`, no `exec`/`create`/`patch`/`delete`), so the
ClusterRole is `get`/`list`/`watch` only, granted cluster-wide (the agent investigates
whatever namespace an alert/query names, not a fixed one):

- core (`""`): `namespaces`, `pods`, `pods/log`, `services`, `endpoints`, `configmaps`,
  `secrets`, `nodes`, `events`, `persistentvolumeclaims`, `persistentvolumes`,
  `resourcequotas`, `limitranges`
- `apps`: `deployments`, `replicasets`, `statefulsets`, `daemonsets`
- `autoscaling`: `horizontalpodautoscalers`
- `batch`: `jobs`, `cronjobs`
- `networking.k8s.io`: `ingresses`, `networkpolicies`
- `rbac.authorization.k8s.io`: `clusterroles`, `clusterrolebindings`, `roles`, `rolebindings`
- `storage.k8s.io`: `storageclasses`
- `metrics.k8s.io`: `pods`, `nodes` (needed by `top_pods`/`top_nodes`; this API group only
  exists if metrics-server is installed in-cluster — if it's absent, those two tools error at
  call time, which is existing, unchanged application behavior, not something this chart fixes)

### Networking and config wiring

- All Services are `ClusterIP`, named `<release>-<workload>` (e.g. `kubernaut-k8s-mcp-server`)
  so `api`'s `K8S_MCP_SERVER_URL`/`PROMETHEUS_MCP_SERVER_URL` env vars can be computed in-chart
  as `http://<release>-k8s-mcp-server:8000/mcp` / `http://<release>-prometheus-mcp-server:8000/mcp` —
  no manual wiring needed after `helm install`.
- `api`'s secrets (`OPENROUTER_API_KEY`, `DATABASE_URL`, `GH_TOKEN`, `GIT_AUTHOR_NAME`,
  `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`) are injected via
  `envFrom.secretRef.name`, pointing at an **existing** Secret the chart does not create
  (`values.api.existingSecret`, default `kubernaut-api-secrets`). Nothing sensitive is ever
  templated into a chart value or committed to git.
- `prometheusMcpServer.prometheusUrl` (default
  `http://prometheus-server.monitoring.svc.cluster.local:9090`) is a plain value, templated
  into the container's `PROMETHEUS_URL` env var.
- `ui`'s `NEXT_PUBLIC_API_URL` is a Next.js build-time constant (baked into the client JS
  bundle by `ui/Dockerfile`'s `ARG`/`ENV` at `docker build` time) — it **cannot** be changed by
  a chart value at deploy time. `values.yaml` and the generated `NOTES.txt` both call this out
  explicitly so nobody expects `helm upgrade --set ui.apiUrl=...` to do anything; changing it
  requires rebuilding the `ui` image with a new `--build-arg NEXT_PUBLIC_API_URL=...`.

### Probes

No workload has a confirmed plain-HTTP health route: `api` has no `/health` (only
`/chats`, `/diagnose`, `/approve/{thread_id}`, `/chats/{chat_id}/messages`), and both MCP
servers mount their tools at `/mcp`, which is an MCP-protocol endpoint, not a plain-GET-200
path. So `api`, `k8s-mcp-server`, and `prometheus-mcp-server` all get `tcpSocket` readiness +
liveness probes on their container port — this only confirms the process is listening, not
full app health, but it's the only assumption-free option available without adding a real
health route to the app (out of scope here). `ui` gets `httpGet path: /` (Next.js standalone
server serves a real 200 on `/`).

### Resources

Modest default `requests`/`limits` per workload (e.g. `100m`/`128Mi` requests,
`500m`/`512Mi` limits — exact numbers finalized in the plan/implementation, all overridable
per-workload via `values.yaml`).

## values.yaml shape

One top-level key per workload:

```yaml
api:
  image: { repository: kubernaut-api, tag: latest, pullPolicy: IfNotPresent }
  replicaCount: 1
  existingSecret: kubernaut-api-secrets
  resources: {...}

ui:
  image: { repository: kubernaut-ui, tag: latest, pullPolicy: IfNotPresent }
  replicaCount: 1
  resources: {...}
  # NOTE: NEXT_PUBLIC_API_URL is baked in at image build time, not settable here.

k8sMcpServer:
  image: { repository: kubernaut-k8s-mcp-server, tag: latest, pullPolicy: IfNotPresent }
  replicaCount: 1
  resources: {...}

prometheusMcpServer:
  image: { repository: kubernaut-prometheus-mcp-server, tag: latest, pullPolicy: IfNotPresent }
  replicaCount: 1
  prometheusUrl: http://prometheus-server.monitoring.svc.cluster.local:9090
  resources: {...}
```

## Directory layout

```
helm/
  manifests/                      # raw, non-templated, kubectl-apply-able reference
    api-deployment.yaml
    api-service.yaml
    ui-deployment.yaml
    ui-service.yaml
    k8s-mcp-server-serviceaccount.yaml
    k8s-mcp-server-clusterrole.yaml
    k8s-mcp-server-clusterrolebinding.yaml
    k8s-mcp-server-deployment.yaml
    k8s-mcp-server-service.yaml
    prometheus-mcp-server-deployment.yaml
    prometheus-mcp-server-service.yaml
  kubernaut/                      # the packaged Helm chart
    Chart.yaml
    values.yaml
    templates/
      _helpers.tpl
      api-deployment.yaml
      api-service.yaml
      ui-deployment.yaml
      ui-service.yaml
      k8s-mcp-server-serviceaccount.yaml
      k8s-mcp-server-clusterrole.yaml
      k8s-mcp-server-clusterrolebinding.yaml
      k8s-mcp-server-deployment.yaml
      k8s-mcp-server-service.yaml
      prometheus-mcp-server-deployment.yaml
      prometheus-mcp-server-service.yaml
      NOTES.txt
```

## Testing / validation plan

- `helm lint helm/kubernaut` must pass.
- `helm template helm/kubernaut` must render without error, with default values and with a
  values override exercising non-default `image.tag`/`replicaCount`/`existingSecret` to
  confirm templating actually uses the values (not hardcoded).
- Each raw manifest in `helm/manifests/` and each rendered chart template must be valid YAML
  (parses) and, where `kubectl` is available, pass `kubectl apply --dry-run=client -f -`
  against the local kubeconfig context for a structural/schema check.
- No new Python code is introduced, so the existing `pytest` unit suite is unaffected and does
  not need to be re-run for this change.

## Open questions / assumptions carried forward

None outstanding — all prior open questions (chart structure, secrets handling, ingress/DB
scope, image naming) were resolved during brainstorming and are reflected above as decisions,
not TBDs.

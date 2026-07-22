# Kubernaut Helm Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create plain Kubernetes manifests for the `api`, `ui`, `k8s_mcp_server`, and `prometheus_mcp_server` workloads, then package the same resources as a templated Helm chart.

**Architecture:** One umbrella Helm chart (`helm/kubernaut/`) with one Deployment+Service pair per workload (plus a ServiceAccount/ClusterRole/ClusterRoleBinding for `k8s-mcp-server`), values-driven per workload. The same resources are also committed as raw, non-templated YAML under `helm/manifests/` as a plain `kubectl apply -f`-able reference.

**Tech Stack:** Kubernetes manifests (apps/v1, v1, rbac.authorization.k8s.io/v1), Helm 3 (`v3.16.2` confirmed installed locally), `kubectl` (confirmed installed, current context `minikube`), Python 3 + PyYAML (confirmed installed in `.venv`) for YAML-parse checks.

## Global Constraints

- Services are `ClusterIP` only — no Ingress in this chart (spec: "ClusterIP only + Postgres external").
- The chart never creates a Secret. `api`'s `OPENROUTER_API_KEY`/`DATABASE_URL`/`GH_TOKEN`/`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` come from `envFrom.secretRef.name`, referencing an **existing** Secret named by `values.api.existingSecret` (default `kubernaut-api-secrets`).
- No Postgres is deployed by this chart; `DATABASE_URL` in the existing Secret must point at an external/already-running Postgres.
- Images use local names with `tag: latest`, `pullPolicy: IfNotPresent`: `kubernaut-api`, `kubernaut-ui`, `kubernaut-k8s-mcp-server`, `kubernaut-prometheus-mcp-server`.
- `k8s-mcp-server`'s ClusterRole is `get`/`list`/`watch` only (`get`/`list` only for `metrics.k8s.io`), scoped to exactly the resource kinds `k8s_tools.py` shells out to via `kubectl get|describe|logs|top|rollout status --watch=false` (verified: no `exec`/`create`/`patch`/`delete` calls anywhere in that file).
- Probes: `tcpSocket` on container port for `api`, `k8s-mcp-server`, `prometheus-mcp-server` (none of the three has a confirmed plain-HTTP health route); `httpGet path: /` for `ui` (Next.js standalone server serves 200 on `/`).
- `ui`'s `NEXT_PUBLIC_API_URL` is a Next.js build-time constant baked in by `ui/Dockerfile`'s `ARG`/`ENV` — it is not a chart value; this must be documented in `NOTES.txt`, not silently omitted.
- Raw manifests live in `helm/manifests/` (one resource per file); the chart lives in `helm/kubernaut/` (`Chart.yaml`, `values.yaml`, `templates/`).

---

## Task 1: Chart scaffolding (Chart.yaml, values.yaml, _helpers.tpl)

**Files:**
- Create: `helm/kubernaut/Chart.yaml`
- Create: `helm/kubernaut/values.yaml`
- Create: `helm/kubernaut/templates/_helpers.tpl`

**Interfaces:**
- Produces: named templates `kubernaut.name`, `kubernaut.labels`, `kubernaut.selectorLabels` (used by every template in Tasks 2-5). `kubernaut.labels`/`kubernaut.selectorLabels` render `app.kubernetes.io/name`/`app.kubernetes.io/instance` (+ `managed-by` for `labels`) but NOT `app.kubernetes.io/component` — each workload template appends its own `app.kubernetes.io/component: <workload>` line after including these.
- Produces: `values.yaml` keys `api.*`, `ui.*`, `k8sMcpServer.*`, `prometheusMcpServer.*` (exact sub-keys shown below) — every later task's templates read these.

- [ ] **Step 1: Confirm the chart doesn't exist yet (failing check)**

Run: `helm lint helm/kubernaut`
Expected: `Error: unable to check Chart.yaml file in chart: stat helm/kubernaut/Chart.yaml: no such file or directory`

- [ ] **Step 2: Create `helm/kubernaut/Chart.yaml`**

```yaml
apiVersion: v2
name: kubernaut
description: Kubernaut -- autonomous (human-gated) SRE agent for Kubernetes
type: application
version: 0.1.0
appVersion: "0.1.0"
```

- [ ] **Step 3: Create `helm/kubernaut/values.yaml`**

```yaml
api:
  image:
    repository: kubernaut-api
    tag: latest
    pullPolicy: IfNotPresent
  replicaCount: 1
  existingSecret: kubernaut-api-secrets
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

ui:
  image:
    repository: kubernaut-ui
    tag: latest
    pullPolicy: IfNotPresent
  replicaCount: 1
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

k8sMcpServer:
  image:
    repository: kubernaut-k8s-mcp-server
    tag: latest
    pullPolicy: IfNotPresent
  replicaCount: 1
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

prometheusMcpServer:
  image:
    repository: kubernaut-prometheus-mcp-server
    tag: latest
    pullPolicy: IfNotPresent
  replicaCount: 1
  prometheusUrl: http://prometheus-server.monitoring.svc.cluster.local:9090
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

- [ ] **Step 4: Create `helm/kubernaut/templates/_helpers.tpl`**

```
{{- define "kubernaut.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "kubernaut.labels" -}}
app.kubernetes.io/name: {{ include "kubernaut.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "kubernaut.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubernaut.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
```

- [ ] **Step 5: Verify the chart now lints clean**

Run: `helm lint helm/kubernaut`
Expected: `0 chart(s) linted, 0 chart(s) failed` (an info-level "icon is recommended" note is fine, that's not a failure)

- [ ] **Step 6: Commit**

```bash
git add helm/kubernaut/Chart.yaml helm/kubernaut/values.yaml helm/kubernaut/templates/_helpers.tpl
git commit -m "$(cat <<'EOF'
helm: scaffold kubernaut chart (Chart.yaml, values.yaml, helpers)

EOF
)"
```

---

## Task 2: `api` workload (raw manifests + chart templates)

**Files:**
- Create: `helm/manifests/api-deployment.yaml`
- Create: `helm/manifests/api-service.yaml`
- Create: `helm/kubernaut/templates/api-deployment.yaml`
- Create: `helm/kubernaut/templates/api-service.yaml`

**Interfaces:**
- Consumes: `kubernaut.labels`, `kubernaut.selectorLabels` from Task 1; `values.api.*` from Task 1's `values.yaml`.
- Produces: Service `{{ .Release.Name }}-api` on port 8000 — Task 5's `NOTES.txt` references this name.

- [ ] **Step 1: Confirm raw manifest doesn't exist yet (failing check)**

Run: `python3 -c "import yaml; yaml.safe_load(open('helm/manifests/api-deployment.yaml'))"`
Expected: `FileNotFoundError: [Errno 2] No such file or directory: 'helm/manifests/api-deployment.yaml'`

- [ ] **Step 2: Create `helm/manifests/api-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernaut-api
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: api
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: kubernaut
      app.kubernetes.io/component: api
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kubernaut
        app.kubernetes.io/component: api
    spec:
      containers:
        - name: api
          image: kubernaut-api:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
          env:
            - name: K8S_MCP_SERVER_URL
              value: http://kubernaut-k8s-mcp-server:8000/mcp
            - name: PROMETHEUS_MCP_SERVER_URL
              value: http://kubernaut-prometheus-mcp-server:8000/mcp
          envFrom:
            - secretRef:
                name: kubernaut-api-secrets
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
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

- [ ] **Step 3: Create `helm/manifests/api-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: kubernaut-api
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: api
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: api
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 4: Verify both raw manifests parse and pass client-side schema validation**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('helm/manifests/api-deployment.yaml')); yaml.safe_load(open('helm/manifests/api-service.yaml'))" && echo PARSE_OK
kubectl apply --dry-run=client -f helm/manifests/api-deployment.yaml -f helm/manifests/api-service.yaml
```
Expected: `PARSE_OK`, then `deployment.apps/kubernaut-api created (dry run)` and `service/kubernaut-api created (dry run)`

- [ ] **Step 5: Confirm the chart template doesn't exist yet (failing check)**

Run: `helm template kubernaut helm/kubernaut -s templates/api-deployment.yaml`
Expected: `Error: could not find template templates/api-deployment.yaml in chart`

- [ ] **Step 6: Create `helm/kubernaut/templates/api-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-api
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: api
spec:
  replicas: {{ .Values.api.replicaCount }}
  selector:
    matchLabels:
      {{- include "kubernaut.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: api
  template:
    metadata:
      labels:
        {{- include "kubernaut.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: api
    spec:
      containers:
        - name: api
          image: "{{ .Values.api.image.repository }}:{{ .Values.api.image.tag }}"
          imagePullPolicy: {{ .Values.api.image.pullPolicy }}
          ports:
            - containerPort: 8000
          env:
            - name: K8S_MCP_SERVER_URL
              value: http://{{ .Release.Name }}-k8s-mcp-server:8000/mcp
            - name: PROMETHEUS_MCP_SERVER_URL
              value: http://{{ .Release.Name }}-prometheus-mcp-server:8000/mcp
          envFrom:
            - secretRef:
                name: {{ .Values.api.existingSecret }}
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
            {{- toYaml .Values.api.resources | nindent 12 }}
```

- [ ] **Step 7: Create `helm/kubernaut/templates/api-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-api
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: api
spec:
  type: ClusterIP
  selector:
    {{- include "kubernaut.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: api
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 8: Verify the templates render and pick up an overridden value**

Run: `helm template kubernaut helm/kubernaut -s templates/api-deployment.yaml -s templates/api-service.yaml --set api.image.tag=v2.0.0`
Expected: valid YAML output containing `name: kubernaut-api`, `image: "kubernaut-api:v2.0.0"`, `name: kubernaut-api-secrets`, and `value: http://kubernaut-k8s-mcp-server:8000/mcp`

- [ ] **Step 9: Commit**

```bash
git add helm/manifests/api-deployment.yaml helm/manifests/api-service.yaml helm/kubernaut/templates/api-deployment.yaml helm/kubernaut/templates/api-service.yaml
git commit -m "$(cat <<'EOF'
helm: add api manifests and chart templates

EOF
)"
```

---

## Task 3: `ui` workload (raw manifests + chart templates)

**Files:**
- Create: `helm/manifests/ui-deployment.yaml`
- Create: `helm/manifests/ui-service.yaml`
- Create: `helm/kubernaut/templates/ui-deployment.yaml`
- Create: `helm/kubernaut/templates/ui-service.yaml`

**Interfaces:**
- Consumes: `kubernaut.labels`, `kubernaut.selectorLabels` from Task 1; `values.ui.*` from Task 1's `values.yaml`.
- Produces: Service `{{ .Release.Name }}-ui` on port 3000 — Task 5's `NOTES.txt` references this name.

- [ ] **Step 1: Confirm raw manifest doesn't exist yet (failing check)**

Run: `python3 -c "import yaml; yaml.safe_load(open('helm/manifests/ui-deployment.yaml'))"`
Expected: `FileNotFoundError: [Errno 2] No such file or directory: 'helm/manifests/ui-deployment.yaml'`

- [ ] **Step 2: Create `helm/manifests/ui-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernaut-ui
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: ui
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: kubernaut
      app.kubernetes.io/component: ui
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kubernaut
        app.kubernetes.io/component: ui
    spec:
      containers:
        - name: ui
          image: kubernaut-ui:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 3000
          readinessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 10
            periodSeconds: 20
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

- [ ] **Step 3: Create `helm/manifests/ui-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: kubernaut-ui
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: ui
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: ui
  ports:
    - port: 3000
      targetPort: 3000
```

- [ ] **Step 4: Verify both raw manifests parse and pass client-side schema validation**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('helm/manifests/ui-deployment.yaml')); yaml.safe_load(open('helm/manifests/ui-service.yaml'))" && echo PARSE_OK
kubectl apply --dry-run=client -f helm/manifests/ui-deployment.yaml -f helm/manifests/ui-service.yaml
```
Expected: `PARSE_OK`, then `deployment.apps/kubernaut-ui created (dry run)` and `service/kubernaut-ui created (dry run)`

- [ ] **Step 5: Confirm the chart template doesn't exist yet (failing check)**

Run: `helm template kubernaut helm/kubernaut -s templates/ui-deployment.yaml`
Expected: `Error: could not find template templates/ui-deployment.yaml in chart`

- [ ] **Step 6: Create `helm/kubernaut/templates/ui-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-ui
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: ui
spec:
  replicas: {{ .Values.ui.replicaCount }}
  selector:
    matchLabels:
      {{- include "kubernaut.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: ui
  template:
    metadata:
      labels:
        {{- include "kubernaut.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: ui
    spec:
      containers:
        - name: ui
          image: "{{ .Values.ui.image.repository }}:{{ .Values.ui.image.tag }}"
          imagePullPolicy: {{ .Values.ui.image.pullPolicy }}
          ports:
            - containerPort: 3000
          readinessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 10
            periodSeconds: 20
          resources:
            {{- toYaml .Values.ui.resources | nindent 12 }}
```

- [ ] **Step 7: Create `helm/kubernaut/templates/ui-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-ui
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: ui
spec:
  type: ClusterIP
  selector:
    {{- include "kubernaut.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: ui
  ports:
    - port: 3000
      targetPort: 3000
```

- [ ] **Step 8: Verify the templates render and pick up an overridden value**

Run: `helm template kubernaut helm/kubernaut -s templates/ui-deployment.yaml -s templates/ui-service.yaml --set ui.replicaCount=3`
Expected: valid YAML output containing `name: kubernaut-ui`, `image: "kubernaut-ui:latest"`, and `replicas: 3`

- [ ] **Step 9: Commit**

```bash
git add helm/manifests/ui-deployment.yaml helm/manifests/ui-service.yaml helm/kubernaut/templates/ui-deployment.yaml helm/kubernaut/templates/ui-service.yaml
git commit -m "$(cat <<'EOF'
helm: add ui manifests and chart templates

EOF
)"
```

---

## Task 4: `k8s-mcp-server` workload (ServiceAccount, ClusterRole, ClusterRoleBinding, raw manifests + chart templates)

**Files:**
- Create: `helm/manifests/k8s-mcp-server-serviceaccount.yaml`
- Create: `helm/manifests/k8s-mcp-server-clusterrole.yaml`
- Create: `helm/manifests/k8s-mcp-server-clusterrolebinding.yaml`
- Create: `helm/manifests/k8s-mcp-server-deployment.yaml`
- Create: `helm/manifests/k8s-mcp-server-service.yaml`
- Create: `helm/kubernaut/templates/k8s-mcp-server-serviceaccount.yaml`
- Create: `helm/kubernaut/templates/k8s-mcp-server-clusterrole.yaml`
- Create: `helm/kubernaut/templates/k8s-mcp-server-clusterrolebinding.yaml`
- Create: `helm/kubernaut/templates/k8s-mcp-server-deployment.yaml`
- Create: `helm/kubernaut/templates/k8s-mcp-server-service.yaml`

**Interfaces:**
- Consumes: `kubernaut.labels`, `kubernaut.selectorLabels` from Task 1; `values.k8sMcpServer.*` from Task 1's `values.yaml`.
- Produces: Service `{{ .Release.Name }}-k8s-mcp-server` on port 8000 (referenced by Task 2's `api` template's `K8S_MCP_SERVER_URL`, already wired).

- [ ] **Step 1: Confirm raw manifests don't exist yet (failing check)**

Run: `python3 -c "import yaml; yaml.safe_load(open('helm/manifests/k8s-mcp-server-serviceaccount.yaml'))"`
Expected: `FileNotFoundError: [Errno 2] No such file or directory: 'helm/manifests/k8s-mcp-server-serviceaccount.yaml'`

- [ ] **Step 2: Create `helm/manifests/k8s-mcp-server-serviceaccount.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubernaut-k8s-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
```

- [ ] **Step 3: Create `helm/manifests/k8s-mcp-server-clusterrole.yaml`**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubernaut-k8s-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
rules:
  - apiGroups: [""]
    resources:
      - namespaces
      - pods
      - pods/log
      - services
      - endpoints
      - configmaps
      - secrets
      - nodes
      - events
      - persistentvolumeclaims
      - persistentvolumes
      - resourcequotas
      - limitranges
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources:
      - deployments
      - replicasets
      - statefulsets
      - daemonsets
    verbs: ["get", "list", "watch"]
  - apiGroups: ["autoscaling"]
    resources:
      - horizontalpodautoscalers
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources:
      - jobs
      - cronjobs
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources:
      - ingresses
      - networkpolicies
    verbs: ["get", "list", "watch"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources:
      - clusterroles
      - clusterrolebindings
      - roles
      - rolebindings
    verbs: ["get", "list", "watch"]
  - apiGroups: ["storage.k8s.io"]
    resources:
      - storageclasses
    verbs: ["get", "list", "watch"]
  - apiGroups: ["metrics.k8s.io"]
    resources:
      - pods
      - nodes
    verbs: ["get", "list"]
```

- [ ] **Step 4: Create `helm/manifests/k8s-mcp-server-clusterrolebinding.yaml`**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubernaut-k8s-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubernaut-k8s-mcp-server
subjects:
  - kind: ServiceAccount
    name: kubernaut-k8s-mcp-server
    namespace: default
```

- [ ] **Step 5: Create `helm/manifests/k8s-mcp-server-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernaut-k8s-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: kubernaut
      app.kubernetes.io/component: k8s-mcp-server
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kubernaut
        app.kubernetes.io/component: k8s-mcp-server
    spec:
      serviceAccountName: kubernaut-k8s-mcp-server
      containers:
        - name: k8s-mcp-server
          image: kubernaut-k8s-mcp-server:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
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
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

- [ ] **Step 6: Create `helm/manifests/k8s-mcp-server-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: kubernaut-k8s-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: k8s-mcp-server
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 7: Verify all five raw manifests parse and pass client-side schema validation**

Run:
```bash
python3 -c "
import yaml
for f in ['helm/manifests/k8s-mcp-server-serviceaccount.yaml', 'helm/manifests/k8s-mcp-server-clusterrole.yaml', 'helm/manifests/k8s-mcp-server-clusterrolebinding.yaml', 'helm/manifests/k8s-mcp-server-deployment.yaml', 'helm/manifests/k8s-mcp-server-service.yaml']:
    yaml.safe_load(open(f))
print('PARSE_OK')
"
kubectl apply --dry-run=client \
  -f helm/manifests/k8s-mcp-server-serviceaccount.yaml \
  -f helm/manifests/k8s-mcp-server-clusterrole.yaml \
  -f helm/manifests/k8s-mcp-server-clusterrolebinding.yaml \
  -f helm/manifests/k8s-mcp-server-deployment.yaml \
  -f helm/manifests/k8s-mcp-server-service.yaml
```
Expected: `PARSE_OK`, then one `created (dry run)` line per resource (serviceaccount, clusterrole.rbac.authorization.k8s.io, clusterrolebinding.rbac.authorization.k8s.io, deployment.apps, service), no errors

- [ ] **Step 8: Confirm the chart templates don't exist yet (failing check)**

Run: `helm template kubernaut helm/kubernaut -s templates/k8s-mcp-server-deployment.yaml`
Expected: `Error: could not find template templates/k8s-mcp-server-deployment.yaml in chart`

- [ ] **Step 9: Create `helm/kubernaut/templates/k8s-mcp-server-serviceaccount.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Release.Name }}-k8s-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
```

- [ ] **Step 10: Create `helm/kubernaut/templates/k8s-mcp-server-clusterrole.yaml`**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Name }}-k8s-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
rules:
  - apiGroups: [""]
    resources:
      - namespaces
      - pods
      - pods/log
      - services
      - endpoints
      - configmaps
      - secrets
      - nodes
      - events
      - persistentvolumeclaims
      - persistentvolumes
      - resourcequotas
      - limitranges
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources:
      - deployments
      - replicasets
      - statefulsets
      - daemonsets
    verbs: ["get", "list", "watch"]
  - apiGroups: ["autoscaling"]
    resources:
      - horizontalpodautoscalers
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources:
      - jobs
      - cronjobs
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources:
      - ingresses
      - networkpolicies
    verbs: ["get", "list", "watch"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources:
      - clusterroles
      - clusterrolebindings
      - roles
      - rolebindings
    verbs: ["get", "list", "watch"]
  - apiGroups: ["storage.k8s.io"]
    resources:
      - storageclasses
    verbs: ["get", "list", "watch"]
  - apiGroups: ["metrics.k8s.io"]
    resources:
      - pods
      - nodes
    verbs: ["get", "list"]
```

- [ ] **Step 11: Create `helm/kubernaut/templates/k8s-mcp-server-clusterrolebinding.yaml`**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ .Release.Name }}-k8s-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ .Release.Name }}-k8s-mcp-server
subjects:
  - kind: ServiceAccount
    name: {{ .Release.Name }}-k8s-mcp-server
    namespace: {{ .Release.Namespace }}
```

- [ ] **Step 12: Create `helm/kubernaut/templates/k8s-mcp-server-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-k8s-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
spec:
  replicas: {{ .Values.k8sMcpServer.replicaCount }}
  selector:
    matchLabels:
      {{- include "kubernaut.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: k8s-mcp-server
  template:
    metadata:
      labels:
        {{- include "kubernaut.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: k8s-mcp-server
    spec:
      serviceAccountName: {{ .Release.Name }}-k8s-mcp-server
      containers:
        - name: k8s-mcp-server
          image: "{{ .Values.k8sMcpServer.image.repository }}:{{ .Values.k8sMcpServer.image.tag }}"
          imagePullPolicy: {{ .Values.k8sMcpServer.image.pullPolicy }}
          ports:
            - containerPort: 8000
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
            {{- toYaml .Values.k8sMcpServer.resources | nindent 12 }}
```

- [ ] **Step 13: Create `helm/kubernaut/templates/k8s-mcp-server-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-k8s-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
spec:
  type: ClusterIP
  selector:
    {{- include "kubernaut.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: k8s-mcp-server
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 14: Verify the templates render, wire the ServiceAccount, and pick up an overridden namespace**

Run: `helm template kubernaut helm/kubernaut --namespace kubernaut-system -s templates/k8s-mcp-server-serviceaccount.yaml -s templates/k8s-mcp-server-clusterrole.yaml -s templates/k8s-mcp-server-clusterrolebinding.yaml -s templates/k8s-mcp-server-deployment.yaml -s templates/k8s-mcp-server-service.yaml`
Expected: valid YAML output containing `serviceAccountName: kubernaut-k8s-mcp-server`, `namespace: kubernaut-system` (in the ClusterRoleBinding subject), and `apiGroups:` entries for `""`, `apps`, `autoscaling`, `batch`, `networking.k8s.io`, `rbac.authorization.k8s.io`, `storage.k8s.io`, `metrics.k8s.io`

- [ ] **Step 15: Commit**

```bash
git add helm/manifests/k8s-mcp-server-serviceaccount.yaml helm/manifests/k8s-mcp-server-clusterrole.yaml helm/manifests/k8s-mcp-server-clusterrolebinding.yaml helm/manifests/k8s-mcp-server-deployment.yaml helm/manifests/k8s-mcp-server-service.yaml helm/kubernaut/templates/k8s-mcp-server-serviceaccount.yaml helm/kubernaut/templates/k8s-mcp-server-clusterrole.yaml helm/kubernaut/templates/k8s-mcp-server-clusterrolebinding.yaml helm/kubernaut/templates/k8s-mcp-server-deployment.yaml helm/kubernaut/templates/k8s-mcp-server-service.yaml
git commit -m "$(cat <<'EOF'
helm: add k8s-mcp-server manifests, RBAC, and chart templates

EOF
)"
```

---

## Task 5: `prometheus-mcp-server` workload (raw manifests + chart templates)

**Files:**
- Create: `helm/manifests/prometheus-mcp-server-deployment.yaml`
- Create: `helm/manifests/prometheus-mcp-server-service.yaml`
- Create: `helm/kubernaut/templates/prometheus-mcp-server-deployment.yaml`
- Create: `helm/kubernaut/templates/prometheus-mcp-server-service.yaml`

**Interfaces:**
- Consumes: `kubernaut.labels`, `kubernaut.selectorLabels` from Task 1; `values.prometheusMcpServer.*` from Task 1's `values.yaml`.
- Produces: Service `{{ .Release.Name }}-prometheus-mcp-server` on port 8000 (referenced by Task 2's `api` template's `PROMETHEUS_MCP_SERVER_URL`, already wired).

- [ ] **Step 1: Confirm raw manifest doesn't exist yet (failing check)**

Run: `python3 -c "import yaml; yaml.safe_load(open('helm/manifests/prometheus-mcp-server-deployment.yaml'))"`
Expected: `FileNotFoundError: [Errno 2] No such file or directory: 'helm/manifests/prometheus-mcp-server-deployment.yaml'`

- [ ] **Step 2: Create `helm/manifests/prometheus-mcp-server-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernaut-prometheus-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: prometheus-mcp-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: kubernaut
      app.kubernetes.io/component: prometheus-mcp-server
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kubernaut
        app.kubernetes.io/component: prometheus-mcp-server
    spec:
      containers:
        - name: prometheus-mcp-server
          image: kubernaut-prometheus-mcp-server:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
          env:
            - name: PROMETHEUS_URL
              value: http://prometheus-server.monitoring.svc.cluster.local:9090
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
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

- [ ] **Step 3: Create `helm/manifests/prometheus-mcp-server-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: kubernaut-prometheus-mcp-server
  labels:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: prometheus-mcp-server
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: kubernaut
    app.kubernetes.io/component: prometheus-mcp-server
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 4: Verify both raw manifests parse and pass client-side schema validation**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('helm/manifests/prometheus-mcp-server-deployment.yaml')); yaml.safe_load(open('helm/manifests/prometheus-mcp-server-service.yaml'))" && echo PARSE_OK
kubectl apply --dry-run=client -f helm/manifests/prometheus-mcp-server-deployment.yaml -f helm/manifests/prometheus-mcp-server-service.yaml
```
Expected: `PARSE_OK`, then `deployment.apps/kubernaut-prometheus-mcp-server created (dry run)` and `service/kubernaut-prometheus-mcp-server created (dry run)`

- [ ] **Step 5: Confirm the chart template doesn't exist yet (failing check)**

Run: `helm template kubernaut helm/kubernaut -s templates/prometheus-mcp-server-deployment.yaml`
Expected: `Error: could not find template templates/prometheus-mcp-server-deployment.yaml in chart`

- [ ] **Step 6: Create `helm/kubernaut/templates/prometheus-mcp-server-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-prometheus-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: prometheus-mcp-server
spec:
  replicas: {{ .Values.prometheusMcpServer.replicaCount }}
  selector:
    matchLabels:
      {{- include "kubernaut.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: prometheus-mcp-server
  template:
    metadata:
      labels:
        {{- include "kubernaut.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: prometheus-mcp-server
    spec:
      containers:
        - name: prometheus-mcp-server
          image: "{{ .Values.prometheusMcpServer.image.repository }}:{{ .Values.prometheusMcpServer.image.tag }}"
          imagePullPolicy: {{ .Values.prometheusMcpServer.image.pullPolicy }}
          ports:
            - containerPort: 8000
          env:
            - name: PROMETHEUS_URL
              value: {{ .Values.prometheusMcpServer.prometheusUrl | quote }}
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
            {{- toYaml .Values.prometheusMcpServer.resources | nindent 12 }}
```

- [ ] **Step 7: Create `helm/kubernaut/templates/prometheus-mcp-server-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-prometheus-mcp-server
  labels:
    {{- include "kubernaut.labels" . | nindent 4 }}
    app.kubernetes.io/component: prometheus-mcp-server
spec:
  type: ClusterIP
  selector:
    {{- include "kubernaut.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: prometheus-mcp-server
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 8: Verify the templates render and pick up an overridden value**

Run: `helm template kubernaut helm/kubernaut -s templates/prometheus-mcp-server-deployment.yaml -s templates/prometheus-mcp-server-service.yaml --set prometheusMcpServer.prometheusUrl=http://prom.other.svc:9090`
Expected: valid YAML output containing `name: kubernaut-prometheus-mcp-server` and `value: http://prom.other.svc:9090`

- [ ] **Step 9: Commit**

```bash
git add helm/manifests/prometheus-mcp-server-deployment.yaml helm/manifests/prometheus-mcp-server-service.yaml helm/kubernaut/templates/prometheus-mcp-server-deployment.yaml helm/kubernaut/templates/prometheus-mcp-server-service.yaml
git commit -m "$(cat <<'EOF'
helm: add prometheus-mcp-server manifests and chart templates

EOF
)"
```

---

## Task 6: NOTES.txt and full-chart validation

**Files:**
- Create: `helm/kubernaut/templates/NOTES.txt`

**Interfaces:**
- Consumes: `.Values.api.existingSecret` from Task 1; Service names produced by Tasks 2, 3, 4, 5.

- [ ] **Step 1: Confirm NOTES.txt doesn't exist yet (failing check)**

Run: `test -f helm/kubernaut/templates/NOTES.txt && echo EXISTS || echo MISSING`
Expected: `MISSING`

- [ ] **Step 2: Create `helm/kubernaut/templates/NOTES.txt`**

```
Kubernaut has been deployed as release "{{ .Release.Name }}" in namespace "{{ .Release.Namespace }}".

Workloads:
  - {{ .Release.Name }}-api                   (ClusterIP :8000)
  - {{ .Release.Name }}-ui                     (ClusterIP :3000)
  - {{ .Release.Name }}-k8s-mcp-server         (ClusterIP :8000, read-only cluster RBAC)
  - {{ .Release.Name }}-prometheus-mcp-server  (ClusterIP :8000)

Before this release is usable, create the Secret it expects (this chart does not create it):

  kubectl create secret generic {{ .Values.api.existingSecret }} \
    --namespace {{ .Release.Namespace }} \
    --from-literal=OPENROUTER_API_KEY=... \
    --from-literal=DATABASE_URL=... \
    --from-literal=GH_TOKEN=... \
    --from-literal=GIT_AUTHOR_NAME=... \
    --from-literal=GIT_AUTHOR_EMAIL=... \
    --from-literal=GIT_COMMITTER_NAME=... \
    --from-literal=GIT_COMMITTER_EMAIL=...

IMPORTANT: ui's NEXT_PUBLIC_API_URL is baked into the image at `docker build` time
(a Next.js NEXT_PUBLIC_* constraint) -- it is NOT settable via `helm install --set` or
values.yaml. To point the UI at a different API URL, rebuild the ui image with:

  docker build -f ui/Dockerfile -t <repo>:<tag> \
    --build-arg NEXT_PUBLIC_API_URL=https://your-api-host ui/

No Ingress is created by this chart. Access services in-cluster, or via:

  kubectl port-forward svc/{{ .Release.Name }}-ui 3000:3000 --namespace {{ .Release.Namespace }}
  kubectl port-forward svc/{{ .Release.Name }}-api 8000:8000 --namespace {{ .Release.Namespace }}
```

- [ ] **Step 3: Full chart lint**

Run: `helm lint helm/kubernaut`
Expected: `0 chart(s) linted, 0 chart(s) failed`

- [ ] **Step 4: Full chart render with default values — confirm all 11 resources render**

Run: `helm template kubernaut helm/kubernaut | grep -E '^kind: ' | sort | uniq -c`
Expected:
```
      1 kind: ClusterRole
      1 kind: ClusterRoleBinding
      4 kind: Deployment
      4 kind: Service
      1 kind: ServiceAccount
```

- [ ] **Step 5: Full chart render with overridden values — confirm overrides propagate across workloads**

Run: `helm template kubernaut helm/kubernaut --set api.image.tag=v1.2.3 --set ui.replicaCount=2 --set k8sMcpServer.image.repository=ghcr.io/example/k8s-mcp-server --set prometheusMcpServer.prometheusUrl=http://prom.example:9090 | grep -E 'image: |replicas: |value: http://prom'`
Expected: output includes `image: "kubernaut-api:v1.2.3"`, `replicas: 2`, `image: "ghcr.io/example/k8s-mcp-server:latest"`, and `value: http://prom.example:9090`

- [ ] **Step 6: Validate every raw manifest in `helm/manifests/` together**

Run:
```bash
python3 -c "
import glob, yaml
for f in sorted(glob.glob('helm/manifests/*.yaml')):
    yaml.safe_load(open(f))
print('ALL_PARSE_OK')
"
kubectl apply --dry-run=client -f helm/manifests/
```
Expected: `ALL_PARSE_OK`, then one `created (dry run)` line per resource in `helm/manifests/` (11 lines total), no errors

- [ ] **Step 7: Confirm the existing unit test suite is unaffected (no Python source changed by this plan)**

Run: `source .venv/bin/activate && pytest -q 2>&1 | tail -5`
Expected: `155 passed, 10 deselected` (same count as before this plan — this task adds no Python code)

- [ ] **Step 8: Commit**

```bash
git add helm/kubernaut/templates/NOTES.txt
git commit -m "$(cat <<'EOF'
helm: add NOTES.txt and complete kubernaut chart

EOF
)"
```

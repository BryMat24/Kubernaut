# Kubernaut

An autonomous (human-gated) SRE agent for Kubernetes that answers questions, diagnoses incidents from chat or Prometheus alerts, plans remediations, and ships GitOps fixes as GitHub PRs.

---

## 1. Scope

- Chat-based Q&A over live cluster state ("what namespace is pod X in?").
- Diagnose using cluster state (pods, events, describe), logs (Loki) and prometheus alerts
- Produce a remediation plan and, when the fix is a config/manifest change, open - a GitHub PR via a coding agent.
  Be safe by default: read-then-act, human approval before any mutation, full audit trail.

## 2. MVP Architecture Diagram

```mermaid
flowchart TB
    subgraph Ingress["Ingress (chat only)"]
        Chat["Chat: Slack / CLI"]
    end

    subgraph Gateway["API Server"]
        API["FastAPI + LangServe"]
    end

    subgraph Orchestrator["LangGraph Supervisor"]
        Router["Intent Router"]
        HITL{"HITL Approval Gate"}
    end

    QA["Query Agent (read-only)"]

    subgraph DiagFlow["Diagnosis"]
        Evidence["Evidence Collector<br/>pods / events / logs / metrics"]
        Diag["Diagnosis Agent (RCA)"]
    end

    Planner["Remediation Planner<br/>(gitops fix only)"]

    subgraph CodeFlow["GitOps Coding Agent (ephemeral K8s Job)"]
        Coder["Coding Agent<br/>edit manifests"]
        Validate{"Validate:<br/>kubeconform / kustomize"}
        MaxIter{"Max iterations?"}
        PR["Open / Update PR"]
    end

    subgraph MCP["Tool Layer"]
        K8sMCP["Kubernetes<br/>(read verbs + logs)"]
        PromMCP["Prometheus<br/>(metrics)"]
        GHMCP["GitHub<br/>(no cluster creds)"]
    end

    subgraph State["State & Observability"]
        CP["Postgres Checkpointer"]
        Trace["LangSmith Tracing"]
    end

    subgraph Ext["External"]
        K8s["Kubernetes API"]
        Prom["Prometheus"]
        GH["GitHub Manifests Repo"]
        CD["Argo CD / Flux"]
    end

    Chat --> API --> Router
    Router -->|informational| QA
    Router -->|issue detected| Evidence

    QA --> K8sMCP
    QA -->|answer| API

    Evidence --> K8sMCP
    Evidence --> PromMCP
    Evidence --> Diag
    Diag -->|RCA + evidence| Planner

    Planner -->|gitops fix needed| HITL
    Planner -->|no action / info| API
    HITL -->|approved| Coder
    HITL -->|rejected| API

    Coder --> GHMCP
    Coder --> Validate
    Validate -->|fail| MaxIter
    MaxIter -->|no: retry with errors| Coder
    MaxIter -->|yes: escalate to human| API
    Validate -->|pass| PR --> GHMCP

    K8sMCP --> K8s
    PromMCP --> Prom
    GHMCP --> GH --> CD --> K8s

    Router -.state.-> CP
    Coder -.state.-> CP
    HITL -.interrupt/resume.-> CP
    Orchestrator -.trace.-> Trace
```

---

## 3. Deployment

- Ship Kubernaut as an umbrella Helm chart deployed into a dedicated kubernaut-system namespace.

```
kubernaut/                      # umbrella chart
├── charts/
│   ├── orchestrator/           # API server + LangGraph app (Deployment)
│   ├── mcp-kubernetes/         # Deployment + tight RBAC (read + gated write)
│   ├── mcp-prometheus/         # Deployment (read-only)
│   ├── mcp-loki/               # Deployment (read-only)
│   ├── mcp-github/             # Deployment (no cluster RBAC; GitHub token only)
│   └── postgres/               # checkpointer + audit (or external managed)
└── values.yaml
```

- Each MCP deployed as seperate deployment for indpendent scaling, RBAC and blast radius
- Gitops coding agent is deployed as an ephemeral job deployed by the orchestrator per run

## 4. Tech Stack Summary

| Layer             | Choice                                                                           |
| ----------------- | -------------------------------------------------------------------------------- |
| Orchestration     | **LangGraph** (supervisor + subgraphs, Postgres checkpointer, HITL interrupts)   |
| Serving           | FastAPI / LangServe                                                              |
| Tools             | **MCP servers**: Kubernetes, Prometheus, Loki, GitHub (`langchain-mcp-adapters`) |
| RAG               | pgvector or Qdrant + embedding/ingestion pipeline                                |
| State/Memory      | Postgres (checkpointer + audit)                                                  |
| Observability     | LangSmith + OpenTelemetry                                                        |
| Policy/Validation | OPA/Conftest, kubeconform, helm, kustomize                                       |
| Delivery          | GitHub PRs → Argo CD (GitOps)                                                    |
| Packaging         | Helm umbrella chart; coding agent as per-run K8s `Job`                           |
| Secrets           | External Secrets Operator / Vault                                                |

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
    subgraph ENTRY["Entry points"]
        WEBHOOK[Alertmanager webhook]
        CHAT[Chat / CLI]
    end

    WEBHOOK --> SUP
    CHAT --> SUP

    subgraph AGENT_POD["diagnosis-agent pod — monolith, one LangGraph process"]
        direction TB

        subgraph SUP_LOOP["Supervisor — own reasoning loop"]
            SUP[Reasoning node]
            SUP -->|calls sub-agent as tool| DISPATCH{Which agent?}
        end

        DISPATCH -->|scheduling, crash,<br/>resource state| K8S_AGENT
        DISPATCH -->|latency, error rate,<br/>resource trend| OBS_AGENT
        DISPATCH -->|recent change,<br/>deploy correlation| GITOPS_AGENT

        K8S_AGENT -->|findings| SUP
        OBS_AGENT -->|findings| SUP
        GITOPS_AGENT -->|findings| SUP

        SUP -->|enough evidence,<br/>root cause established| REMED_GATE{Remediate?}
        REMED_GATE -->|yes| REMED_AGENT
        REMED_GATE -->|diagnosis only| DONE([Report to user])

        subgraph K8S_AGENT["K8s Agent — read-only"]
            K8S_LOOP[reasoning loop]
        end

        subgraph OBS_AGENT["Observability Agent — read-only"]
            OBS_LOOP[reasoning loop]
        end

        subgraph GITOPS_AGENT["GitOps Investigation Agent — read-only"]
            GI_LOOP[reasoning loop:<br/>recent commits, ArgoCD sync,<br/>deployment history]
        end

        subgraph REMED_AGENT["Remediation Agent — write-capable"]
            RD[Draft fix<br/>local coding tools]
            RD --> RGATE[[HITL interrupt]]
            RGATE -->|approved| RE[Execute: commit + PR]
            RGATE -->|rejected| RR[Return, no mutation]
        end

        REMED_AGENT -->|PR link / rejection| DONE2([Report to user])

        CHECKPOINT[(Checkpointer<br/>short-term memory<br/>+ HITL resume state)]
        VECTOR[(Vector store<br/>long-term memory<br/>runbooks, past incidents)]

        SUP -.-> CHECKPOINT
        RGATE -.-> CHECKPOINT
        K8S_LOOP -.retrieval.-> VECTOR
        OBS_LOOP -.retrieval.-> VECTOR
        RD -.local tools.-> CODING[coding_tools.py<br/>list/read/grep/edit files]
    end

    K8S_LOOP -->|MCP, streamable_http| K8S_MCP
    OBS_LOOP -->|MCP| PROM_MCP
    OBS_LOOP -->|MCP| LOKI_MCP
    GI_LOOP -->|MCP| GH_READ_MCP
    RE -->|MCP| GH_WRITE_MCP

    subgraph MCP_TIER["MCP servers — separate pods, scoped RBAC"]
        K8S_MCP[k8s-mcp-server<br/>read-only ServiceAccount]
        PROM_MCP[prometheus-mcp-server]
        LOKI_MCP[loki-mcp-server]
        GH_READ_MCP[github-mcp-server<br/>read: commits, ArgoCD status]
        GH_WRITE_MCP[github-mcp-server<br/>write: branch, commit, PR]
    end

    K8S_MCP --> K8SAPI[(Kubernetes API)]
    PROM_MCP --> PROM[(Prometheus)]
    LOKI_MCP --> LOKI[(Loki)]
    GH_READ_MCP --> GHUB[(GitHub / ArgoCD)]
    GH_WRITE_MCP --> GHUB
```

---

## 3. Tech Stack Summary

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

# Kubernaut

An autonomous (human-gated) SRE agent for Kubernetes that answers questions, diagnoses incidents from chat or Prometheus alerts, plans remediations, and ships GitOps fixes as GitHub PRs.

---

## 1. Functionality

- Chat-based Q&A over live cluster state ("what namespace is pod X in?", "why is the backend-deployment keeps on failing on dev namespace?").
- Diagnose using cluster state (pods, events, describe), Prometheus metrics, and Loki logs — following a playbook per failure category (scheduling, storage, networking, RBAC, resource governance, rollout, autoscaling, generic Pod Lifecycle).
- Produce a remediation plan and, when the fix is a config/manifest change, open a GitHub PR. If the plan needs one concrete value it can't determine itself (e.g. a valid image tag), it asks the human that specific question instead of guessing or proposing an unexecutable plan.

## 2. Architecture Diagram

```mermaid
flowchart TB
    UI[Next.js chat UI, or any HTTP client] -->|POST /diagnose — SSE| API

    subgraph API_LAYER["FastAPI — api/main.py"]
        API["/diagnose · /approve/{thread_id} · /answer/{thread_id}"]
    end

    API -->|query, repo_url| GRAPH

    subgraph GRAPH["Orchestrator graph — graph/builder.py, one LangGraph process"]
        direction TB

        subgraph DIAG["DiagnosisAgent — read-only, SRE phase graph"]
            direction TB
            INTENT[intent_node] --> INTENT_ROUTE{diagnose or explain?}
            INTENT_ROUTE -->|explain| EXPLAIN[explain_node<br/>bounded ReAct, general knowledge]
            INTENT_ROUTE -->|diagnose| SCOPE[_context_builder<br/>broad scope summary]
            SCOPE --> HYPO[hypothesize_node<br/>pick a playbook + leading hypothesis]
            HYPO --> INVEST[investigate_node ⇄ tool_node<br/>work the playbook's checklist]
            INVEST --> DIAG_EVAL{evaluate_node:<br/>confident, evidence-backed finding?}
            DIAG_EVAL -->|reformulate, budget left| HYPO
            DIAG_EVAL -->|conclusive or exhausted| DIAG_FIN[finalize_node<br/>DiagnosisSummarizer]
            EXPLAIN --> DIAG_FIN
        end

        DIAG_FIN --> ROUTE{require_remediation_routing_node:<br/>diagnosis_success AND requires_remediation?}
        ROUTE -->|no| DONE1([Report diagnosis to user])
        ROUTE -->|yes| PLAN_LOOP

        subgraph PLAN["PlannerAgent — read-only, own git worktree (plan/...)"]
            PLAN_LOOP[reasoning ⇄ tool loop<br/>find / grep / list_files / read_file]
            PLAN_LOOP --> PLAN_FIN[finalize_node<br/>PlanClassifier to RemediationPlan]
        end

        PLAN_FIN --> PLAN_ROUTE{planning_outcome_routing}
        PLAN_ROUTE -->|missing one concrete value,<br/>rounds left| ASK
        PLAN_ROUTE -->|can't produce a safe plan| DONE1B([Report, no mutation])
        PLAN_ROUTE -->|ready| GATE

        ASK[[missing_info_node<br/>HITL interrupt: ask the human]]
        ASK -->|human answers| PLAN_LOOP

        GATE[[human_approval_node<br/>HITL interrupt: diagnosis + plan]]
        GATE -->|approved| REMED_LOOP
        GATE -->|rejected| DONE2([Report diagnosis + plan,<br/>no mutation])

        subgraph REMED["RemediationAgent — write-capable, own git worktree (agent/...)"]
            REMED_LOOP[reasoning ⇄ tool loop<br/>read / edit / write files]
            REMED_LOOP --> EVAL{evaluation_node:<br/>YAML syntax + DiffEvaluator judge}
            EVAL -->|fail, feedback loop| REMED_LOOP
            EVAL -->|pass| PR[pr_node:<br/>commit, push, gh pr create]
        end

        PR --> DONE3([Report PR URL to user])

        CHECKPOINT[(Postgres — AsyncPostgresSaver<br/>+ connection pool<br/>HITL resume state, chat history)]
        GATE -.-> CHECKPOINT
        ASK -.-> CHECKPOINT
    end

    INVEST -->|MCP, streamable_http| K8S_MCP[k8s-mcp-server<br/>read-only, shells to kubectl]
    INVEST -->|MCP, streamable_http| PROM_MCP[prometheus-mcp-server<br/>PromQL over HTTP]
    INVEST -->|MCP, streamable_http| LOKI_MCP[loki-mcp-server<br/>LogQL over HTTP]
    SCOPE -.curated tool subset.-> K8S_MCP
    PLAN_LOOP -.local file tools.-> REPO[(GitOps repo<br/>bare clone + per-task worktree)]
    REMED_LOOP -.local file tools.-> REPO
    PR -->|gh CLI| GHUB[(GitHub)]

    K8S_MCP --> K8SAPI[(Kubernetes API)]
    PROM_MCP --> PROM[(Prometheus)]
    LOKI_MCP --> LOKI[(Loki)]
```

---

## 3. Workflow

1. The user submits a query via the Next.js chat UI (or any HTTP client) — a question or an
   incident description — to FastAPI's `POST /diagnose`, which streams progress back over SSE
   and persists chat history to Postgres.
2. **`DiagnosisAgent`** first classifies intent: a general knowledge question routes to
   `explain_node` (a bounded ReAct loop answering directly, without claiming to have checked a
   live cluster); an actual incident routes into the SRE phase graph — `_context_builder` builds
   a broad, non-diagnosing scope summary, `hypothesize_node` picks the best-matching playbook
   (scheduling, storage, networking, RBAC, resource governance, rollout, autoscaling, or a
   generic Pod Lifecycle fallback) and a leading hypothesis, and `investigate_node` works that
   playbook's checklist against `k8s-mcp-server`, `prometheus-mcp-server`, and `loki-mcp-server`.
   `evaluate_node` judges the result on its own evidentiary merits — a well-evidenced pivot to a
   different mechanism than the one being investigated still counts as conclusive — looping back
   to try a new hypothesis (bounded) if the finding isn't confident yet. Either path ends at
   `finalize_node`, which produces a structured `DiagnosisResult` (summary, root cause, and
   whether the investigation itself was confident/complete).
3. The orchestrator routes on `diagnosis_success` and `requires_remediation`: an inconclusive
   diagnosis, or an "explain" intent (`requires_remediation` is set deterministically to `False`
   for explain-mode results, never left to the LLM), reports back to the user and stops. Any
   other confident diagnosis proceeds to planning — whether the finding is actually fixable via a
   manifest change from there is `PlannerAgent`'s call, not a further pre-gate here.
4. **`PlannerAgent`** clones the GitOps repo into its own isolated, read-only git worktree
   (`plan/...` branch) and investigates which file(s) need to change, then hands its findings to
   `PlanClassifier`, producing a structured `RemediationPlan` — an ordered, file-by-file list of
   concrete steps, or an explanation of what's missing.
5. If the plan is otherwise complete except for one concrete value `PlannerAgent` has no way to
   determine (e.g. a valid replacement image tag), the graph pauses at a **missing-information
   interrupt** asking that specific question, then re-invokes `PlannerAgent` with the human's
   answer folded in — bounded to a couple of rounds so it can't loop forever. If planning fails
   outright with nothing to ask, the graph reports back and stops rather than presenting an
   unexecutable plan.
6. Once a real plan exists, the graph pauses at a **human-in-the-loop approval interrupt**,
   showing both the diagnosis and the proposed plan for review — no mutation happens without
   explicit approval.
7. If the human rejects, the graph ends with nothing changed. If approved, **`RemediationAgent`**
   takes over in its own separate, write-capable git worktree (`agent/...` branch).
8. For each plan step, `RemediationAgent` reads the target file directly (falling back to a
   broader `find`/`grep` investigation only if a step's file isn't where expected) and applies
   the change.
9. Before opening a PR, it validates its own diff — YAML syntax check, then an LLM judge that
   checks the change actually and narrowly addresses the task — looping back to fix issues until
   the diff passes, or until it exhausts its iteration budget.
10. Once the diff passes, it commits, pushes its branch, and opens a GitHub PR via the `gh` CLI,
    then reports the PR URL back to the user.

---

## 4. Tech Stack Summary

| Layer         | Choice                                                                         |
| ------------- | ------------------------------------------------------------------------------ |
| Orchestration | **LangGraph** (supervisor + subgraphs, Postgres checkpointer, HITL interrupts) |
| Serving       | FastAPI (SSE streaming) + Next.js chat UI                                     |
| Tools         | **MCP servers**: Kubernetes, Prometheus, Loki (`langchain-mcp-adapters`)      |
| State/Memory  | Postgres (`AsyncPostgresSaver` + connection pool — checkpointer + chat/audit) |
| Observability | LangSmith + OpenTelemetry                                                      |
| Delivery      | GitHub PRs → Argo CD (GitOps)                                                  |
| Packaging     | Helm umbrella chart                                                            |

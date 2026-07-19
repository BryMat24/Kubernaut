# Kubernaut

An autonomous (human-gated) SRE agent for Kubernetes that answers questions, diagnoses incidents from chat or Prometheus alerts, plans remediations, and ships GitOps fixes as GitHub PRs.

---

## 1. Functionality

- Chat-based Q&A over live cluster state ("what namespace is pod X in?", "why is the backend-deployment keeps on failing on dev namespace?").
- Diagnose using cluster state (pods, events, describe), and prometheus metrics
- Produce a remediation plan and, when the fix is a config/manifest change, open - a GitHub PR.

## 2. Architecture Diagram

```mermaid
flowchart TB
    CLI[CLI — graph/builder.py main] -->|query, repo_url| DIAG_LOOP

    subgraph GRAPH["Orchestrator graph — graph/builder.py, one LangGraph process"]
        direction TB

        subgraph DIAG["DiagnosisAgent — read-only"]
            DIAG_LOOP[reasoning ⇄ tool loop]
            DIAG_LOOP --> DIAG_FIN[finalize_node<br/>Classifier to DiagnosisResult]
        end

        DIAG_FIN --> ROUTE{require_remediation_routing_node:<br/>confident AND nothing to fix?}
        ROUTE -->|yes| DONE1([Report diagnosis to user])
        ROUTE -->|no — fix needed, or<br/>uncertain: never fail-open| PLAN_LOOP

        subgraph PLAN["PlannerAgent — read-only, own git worktree (plan/...)"]
            PLAN_LOOP[reasoning ⇄ tool loop<br/>find / grep / list_files / read_file]
            PLAN_LOOP --> PLAN_FIN[finalize_node<br/>PlanClassifier to RemediationPlan]
        end

        PLAN_FIN --> GATE[[human_approval_node<br/>HITL interrupt: diagnosis + plan]]
        GATE -->|approved| REMED_LOOP
        GATE -->|rejected| DONE2([Report diagnosis + plan,<br/>no mutation])

        subgraph REMED["RemediationAgent — write-capable, own git worktree (agent/...)"]
            REMED_LOOP[reasoning ⇄ tool loop<br/>read / edit / write files]
            REMED_LOOP --> EVAL{evaluation_node:<br/>YAML syntax + DiffEvaluator judge}
            EVAL -->|fail, feedback loop| REMED_LOOP
            EVAL -->|pass| PR[pr_node:<br/>commit, push, gh pr create]
        end

        PR --> DONE3([Report PR URL to user])

        CHECKPOINT[(MemorySaver<br/>in-memory checkpointer<br/>HITL resume state)]
        GATE -.-> CHECKPOINT
    end

    DIAG_LOOP -->|MCP, streamable_http| K8S_MCP[k8s-mcp-server<br/>read-only, shells to kubectl]
    DIAG_LOOP -->|MCP, streamable_http| PROM_MCP[prometheus-mcp-server<br/>PromQL over HTTP]
    PLAN_LOOP -.local file tools.-> REPO[(GitOps repo<br/>bare clone + per-task worktree)]
    REMED_LOOP -.local file tools.-> REPO
    PR -->|gh CLI| GHUB[(GitHub)]

    K8S_MCP --> K8SAPI[(Kubernetes API)]
    PROM_MCP --> PROM[(Prometheus)]
```

---

## 3. Workflow

1. The user submits a query via the CLI (`graph/builder.py`'s `main()`) — a question or an
   incident description — along with the GitOps repo URL to use if remediation turns out to be
   needed.
2. **`DiagnosisAgent`** investigates read-only, in a reasoning ⇄ tool loop against
   `k8s-mcp-server` and `prometheus-mcp-server`, then hands its findings to a classifier LLM
   call that produces a structured `DiagnosisResult` (summary, root cause, whether remediation
   is required, and whether the diagnosis itself was confident/complete).
3. The orchestrator routes on that result: if the diagnosis is confident **and** nothing needs
   fixing, it reports the diagnosis to the user and stops. Otherwise — a real issue was found,
   or the diagnosis was inconclusive — it proceeds to planning. An uncertain diagnosis is never
   treated as "nothing to do."
4. **`PlannerAgent`** clones the GitOps repo into its own isolated, read-only git worktree
   (`plan/...` branch) and investigates which file(s) need to change, then hands its findings to
   its own classifier call, producing a structured `RemediationPlan` — an ordered, file-by-file
   list of concrete steps.
5. The graph pauses at a **human-in-the-loop interrupt**, showing both the diagnosis and the
   proposed plan for review — no mutation happens without explicit approval.
6. If the human rejects, the graph ends with nothing changed. If approved, **`RemediationAgent`**
   takes over in its own separate, write-capable git worktree (`agent/...` branch).
7. For each plan step, `RemediationAgent` reads the target file directly (falling back to a
   broader `find`/`grep` investigation only if a step's file isn't where expected) and applies
   the change.
8. Before opening a PR, it validates its own diff — YAML syntax check, then an LLM judge that
   checks the change actually and narrowly addresses the task — looping back to fix issues until
   the diff passes, or until it exhausts its iteration budget.
9. Once the diff passes, it commits, pushes its branch, and opens a GitHub PR via the `gh` CLI,
   then reports the PR URL back to the user.

---

## 4. Tech Stack Summary

| Layer         | Choice                                                                         |
| ------------- | ------------------------------------------------------------------------------ |
| Orchestration | **LangGraph** (supervisor + subgraphs, Postgres checkpointer, HITL interrupts) |
| Serving       | FastAPI                                                                        |
| Tools         | **MCP servers**: Kubernetes, Prometheus (`langchain-mcp-adapters`)             |
| State/Memory  | Postgres (checkpointer + audit)                                                |
| Observability | LangSmith + OpenTelemetry                                                      |
| Delivery      | GitHub PRs → Argo CD (GitOps)                                                  |
| Packaging     | Helm umbrella chart                                                            |

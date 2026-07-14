# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kubernaut is an autonomous (human-gated) SRE agent for Kubernetes: it answers questions and
diagnoses incidents (chat or Prometheus alerts) using live cluster state, logs, and metrics, then
proposes remediations and — when the fix is a config/manifest change — opens a GitOps PR via a
coding agent. Safety model: read-then-act, human approval before any mutation, full audit trail.
See `README.md` for the full MVP architecture diagram and tech stack table.

## Setup and running

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Requires `OPENROUTER_API_KEY` in `.env` (loaded via `python-dotenv`; see `.env` for the key name).
Agents call OpenRouter models (`ChatOpenRouter`), not the Anthropic API directly.

Run the standalone Kubernetes-agent demo (talks to whatever cluster your kubeconfig points at):

```bash
python workflow.py
```

Run the k8s MCP server standalone (reads `PORT`, default `8080`):

```bash
python mcp_servers/k8s_mcp_server/server.py
```

## Tests

```bash
pytest                                   # unit tests only (integration excluded by default addopts)
pytest test/unit_test/k8s_agent/k8s_tools_test.py::test_list_namespaces_success   # single test
pytest -m integration                    # integration tests: real cluster (current kubectl context) + real LLM calls
```

`pytest.ini` sets `addopts = -m "not integration"`, so integration tests never run unless
explicitly requested with `-m integration`. Integration tests under `test/integration_test/`
spin up the k8s MCP server as a subprocess, apply real manifests to whatever cluster the local
kubeconfig points at, and use an LLM to judge the diagnosis — they cost real API calls and
require a live cluster. Unit tests under `test/unit_test/` mock `subprocess`/tool internals and
have no external dependencies.

`test/conftest.py` inserts the repo root onto `sys.path`; there is no installed package, imports
are always repo-root-relative (`from agents import ...`, `from mcp_clients.k8s_client import ...`).

## Architecture

**Supervisor / sub-agent pattern (LangGraph).** The design (per `README.md`) is a monolithic
LangGraph process with a supervisor that dispatches to read-only investigation sub-agents
(Kubernetes, Observability, GitOps) as tools, then — if a fix is warranted — hands off to a
write-capable Remediation agent gated by a human-in-the-loop interrupt. Only `KubernetesAgent`
(`agents/kubernetes_agent.py`) and `RemediationAgent` (`agents/remediation_agent.py`) are
implemented; `supervisor_agent.py`, `observability_agent.py`, and `gitops_agent.py` are stubs
(empty files) reserved for that architecture.

Each implemented agent is its own `StateGraph` built in `_build_graph`, with a
`reasoning_node` (LLM + bound tools) looping through a `tool_node` (`ToolNode`) until the LLM
stops requesting tool calls or `MAX_ITERATIONS` (30) is hit. State is a `MessagesState` subclass
carrying agent-specific fields (e.g. `KubernetesAgentState.query`, `RemediationAgentState.task`).
Each agent's `SYSTEM_PROMPT` encodes non-obvious domain heuristics (e.g. HPA-vs-metrics-server
triage in `kubernetes_agent.py`) — read it before changing tool-selection or investigation logic.

**KubernetesAgent** is read-only. It gets its tools over MCP (`mcp_clients/k8s_client.py` →
`langchain-mcp-adapters`, `streamable_http` transport) from `mcp_servers/k8s_mcp_server`, a
separate FastMCP process (`server.py` mounts `k8s_tools.py`) that shells out to `kubectl` for
every operation (list/get/describe resources, events, logs, top, rollout status, node
conditions, service connectivity). The agent process and the MCP server are meant to run as
separate pods/processes with scoped RBAC — this separation is intentional, not incidental.

**RemediationAgent** is write-capable and operates on a separate GitOps repo, not the live
cluster. Its graph: `setup_node` (clone/worktree via `utils/git_utils.py`) → `reasoning_node` ↔
`tool_node` (local file tools from `tools/file_tools.py`: `find`, `grep`,
`list_files_in_directory`, `read_file_content`, `edit_file`, `write_file` — no shell/git access
from inside the loop) → `evaluation_node` (YAML syntax check, then an LLM-as-judge diff review
via `evaluator/judge.py::DiffEvaluator`) → loop back to `reasoning_node` on failure, or
`pr_node` (commits, pushes, opens a PR via `gh`) → `cleanup_node` (removes the git worktree).
`utils/git_utils.py` uses a shared bare clone per repo URL (`repo_lock` + `ensure_base_clone`)
with one git worktree per task/branch, so concurrent remediation tasks against the same repo
don't clobber each other.

**Evaluation is LLM-as-judge in two different roles**, both in `evaluator/judge.py`:
`DiffEvaluator` grades whether a remediation diff correctly and narrowly addresses its task
(used inline by `RemediationAgent` to decide whether to loop back or open the PR);
`ScenarioEvaluator` grades whether a `KubernetesAgent` diagnosis matches an expected root cause
(used only in integration tests, comparing against `expected_answer.json` fixtures under
`test/integration_test/k8s_agent/*/cases/`).

**`test_app/`** is a fixture, not application code: three independent FastAPI microservices
(`frontend → backend → cache`) wired as a real HTTP call chain, each with env-var-toggled
fault modes (crash-after-N-seconds, memory leak, slow startup, fail-after-N-requests, readiness
failure — see `test_app/README.md` for the full table), used to give `k8s_agent` integration
tests real cross-service application-level failures instead of faked ones. No pytest suite
covers `test_app/` itself; verification is manual/curl-based per
`docs/superpowers/plans/2026-07-14-test-app-microservice-chain.md`.

## Conventions worth knowing

- File/edit tools (`tools/file_tools.py`) enforce that all paths resolve inside
  `working_directory` (`abspath(...).startswith(abs_working_dir)`) — do not bypass this when
  adding new tools; it's the sandbox boundary for the write-capable agent.
- `edit_file` requires `old_content` to be an exact, unique substring of the file (no fuzzy
  matching) — this mirrors how `RemediationAgent`'s system prompt instructs the LLM to always
  `read_file_content` before `edit_file`.
- Agents log their own reasoning/tool-call trace via `logging` at INFO level with a bare
  `"%(message)s"` format (see `_reasoning_node`/`_tool_node` in both agents) — this is the
  primary way to observe what an agent is doing during a run or test.

# Dummy microservice chain for application-level failure simulation

## Context

`test/integration_test/k8s_agent/cases/*` currently fakes every failure scenario using
stock images (`nginx:latest`, `busybox` loops) manipulated at the Kubernetes-object level
(taints, status patches, resource quotas, RBAC, PVC storage classes, etc.). That approach
covers scheduling/node/RBAC/quota/storage/rollout/autoscaling failures well, but cannot
convincingly fake *application-level* failures, especially ones that cross a real service
boundary — a downstream dependency actually failing and the caller observing a real
symptom, not a simulated one.

A prior attempt (commit `24f9fab`, spec `2026-07-14-test-app-dummy-microservice-design.md`)
built a single env-var-configurable FastAPI image with 6 fault modes. Those files are
deleted in the working tree (uncommitted) and this design supersedes that approach: instead
of one configurable image, `test_app/` now holds 3 separate microservices wired into a real
call chain, so failures propagate through genuine HTTP calls rather than being faked with a
"pretend my dependency is down" flag.

## Goal

Three small, purpose-built FastAPI services deployable as a real dependency chain
(`frontend → backend → cache`), each with 1-2 baked-in, env-var-toggled fault modes (off by
default), reusable across future `k8s_agent` integration test scenarios that need to test
whether the agent can trace a failure across service boundaries — not just diagnose one pod
in isolation.

## Architecture

```
frontend  →  backend  →  cache
(edge)       (logic)     (terminal)
```

- `frontend` calls `backend` on every `/work` request; `backend` calls `cache` on every
  `/work` request; `cache` is the terminal node and makes no further calls.
- Calls are real HTTP calls between real services. If `cache` is OOMKilled, `backend`'s
  calls to it start failing/timing out for real, and that failure surfaces up through
  `frontend` for real — the agent has to trace an actual failure across an actual service
  boundary.
- `frontend` and `backend` read a `DOWNSTREAM_URL` env var (defaults to the in-cluster
  service DNS name, e.g. `http://backend:8000` for frontend, `http://cache:8000` for
  backend) for where to call next.
- Each service exposes:
  - `GET /healthz` — liveness probe target
  - `GET /readyz` — readiness probe target
  - `GET /work` — simulates a normal request; for `frontend`/`backend` this proxies to
    `DOWNSTREAM_URL/work`; for `cache` this does the actual (fake) work and returns

## Per-service fault modes

All env vars default unset (healthy behavior). Multiple vars on the same service are
additive, matching the old design's convention.

| Service | Env var | Effect |
|---|---|---|
| `frontend` | `FAIL_READINESS=true` | `/readyz` always returns 500, `/healthz` stays healthy → stuck-not-Ready pod despite passing liveness |
| `frontend` | `CRASH_AFTER_SECONDS=N` | process exits non-zero after N seconds → CrashLoopBackOff at the edge |
| `backend` | `FAIL_AFTER_N_REQUESTS=N` | `/work` starts returning 500 after N successful calls → intermittent app bug, visible to frontend as a growing error rate |
| `backend` | `MEMORY_LEAK_MB_PER_SEC=N` | background thread grows RSS steadily → real OOMKilled; frontend sees connection resets mid-chain |
| `cache` | `SLOW_STARTUP_SECONDS=N` | delays serving `/healthz`/`/readyz` on boot → startup/readiness timeout; backend's calls to cache hang/timeout, frontend sees cascading latency |
| `cache` | `CRASH_AFTER_SECONDS=N` | process exits non-zero after N seconds → cache CrashLoopBackOff; backend gets connection-refused, frontend sees cascading failure |

## Layout

```
test_app/
├── frontend/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── cache/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
└── README.md          # shared env var reference, build + kind-load instructions,
                        # manual verification steps for all 3 services
```

## Stack

FastAPI + `uvicorn` on `python:3.12-slim`, matching `mcp_servers/k8s_mcp_server/`'s existing
Dockerfile conventions in this repo.

## Build / load into kind

Each image builds and loads independently:

```
docker build -t test-app-frontend:local test_app/frontend/
kind load docker-image test-app-frontend:local
```

(repeated for `backend` and `cache`). Documented once in the shared `test_app/README.md`
rather than duplicated per service. No docker-compose is added — kind is the actual target
environment for these, and local verification runs each container standalone with `docker
run`, not as a wired chain, since in-cluster wiring is out of scope for this task (see
below).

## Testing

Manual verification only, matching the old design's approach: build each image, run it
locally with each of its fault-mode env vars set individually, and confirm `/healthz`,
`/readyz`, and `/work` behave as intended via `curl`. No pytest unit tests are added for
`test_app/` itself — these are test fixtures, not application code under this repo's own
test coverage.

## Out of scope

- No new `cases/*` scenario directories, manifests, or `k8s_agent` integration tests are
  added as part of this task — only the 3 microservices, their Dockerfiles, and shared
  documentation. Wiring the chain into an actual scenario (e.g. cache OOMKilled → backend
  degrades → frontend cascading failure) is a follow-up task once these images build and run
  cleanly.
- The old single-image `test_app/app.py` design and its spec doc are not restored; this
  design supersedes that approach entirely.

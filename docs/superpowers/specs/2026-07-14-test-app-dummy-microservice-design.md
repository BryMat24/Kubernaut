# Dummy microservice for application-level failure simulation

## Context

`test/integration_test/k8s_agent/cases/*` currently fakes every failure scenario using
stock images (`nginx:latest`, `busybox` loops) manipulated at the Kubernetes-object level
(taints, status patches, resource quotas, RBAC, PVC storage classes, etc.). That approach
covers scheduling/node/RBAC/quota/storage/rollout/autoscaling failures well, but cannot
convincingly fake *application-level* failures: a real crash triggered by application logic,
a genuine memory leak that gets OOMKilled, a slow/unreachable dependency causing readiness
timeouts, or an app that degrades after N requests. `test_app/` (currently an empty
directory already scaffolded in the repo) will hold a small, purpose-built microservice that
fakes these convincingly and reproducibly.

## Goal

A single reusable container image, controlled entirely by environment variables, so one
image serves every future application-level failure scenario — the same pattern the existing
suite already uses of varying only the Kubernetes-level manifest per scenario, not the image.

## Stack

FastAPI + `uvicorn` on `python:3.12-slim`, matching `mcp_servers/k8s_mcp_server/`'s existing
Dockerfile conventions in this repo.

## Layout

```
test_app/
├── app.py            # FastAPI app + fault-injection logic
├── requirements.txt
├── Dockerfile
└── README.md         # env var reference for future scenario authors
```

## Endpoints

- `GET /healthz` — liveness probe target
- `GET /readyz` — readiness probe target
- `GET /work` — simulates a normal request; also where request-count-based faults trigger

## Fault modes (env-var driven, all off by default)

| Env var | Effect |
|---|---|
| `CRASH_AFTER_SECONDS=N` | process exits non-zero after N seconds → CrashLoopBackOff |
| `SLOW_STARTUP_SECONDS=N` | delays serving `/healthz`/`/readyz` → startupProbe/readiness timeout |
| `FAIL_READINESS=true` | `/readyz` always returns 500, `/healthz` stays healthy → stuck-not-Ready pod |
| `MEMORY_LEAK_MB_PER_SEC=N` | background thread grows RSS steadily → real OOMKilled |
| `FAIL_AFTER_N_REQUESTS=N` | `/work` starts returning 500 after N calls → intermittent app bug |
| `DEPENDENCY_URL` + `FAIL_DEPENDENCY=true` | `/work` and `/readyz` attempt to reach an unreachable URL and log a connection error → dependency-failure diagnosis |

All fault modes are independent and additive (multiple can be set at once); none are enabled
unless their env var is explicitly set, so the default image behaves as a plain healthy
service.

## Build / load into kind

kind cannot pull from a registry, so the image must be built locally and loaded directly into
the cluster nodes with `kind load docker-image test-app:local`. This exact command is
documented in `test_app/README.md` for scenario authors; no Makefile or CI wiring is added
yet since nothing consumes the image until a scenario is built against it.

## Out of scope

No new `cases/*` scenario directories, manifests, or integration tests are added as part of
this task — only the microservice, its Dockerfile, and its documentation. Wiring the image
into new scenarios is a follow-up once the image builds and runs cleanly.

## Testing

Manual verification only for this task: build the image, run it locally with each fault-mode
env var set individually, and confirm each produces the intended behavior. No pytest unit
tests are added for `test_app/` itself — it's a test fixture, not application code under this
repo's own test coverage.

# test_app — dummy microservice chain for application-level failure simulation

Three FastAPI services wired as a real HTTP call chain, for `k8s_agent` integration test
scenarios that need application-level failures (crash, OOM, slow start, degrade-after-N,
readiness failure) to propagate across a real service boundary instead of being faked.

```
frontend  →  backend  →  cache
(edge)       (logic)     (terminal)
```

Each service exposes `GET /healthz` (liveness), `GET /readyz` (readiness), `GET /work`
(does its job — proxies downstream for frontend/backend, does the actual work for cache).

## Env vars

| Service | Env var | Default | Effect |
|---|---|---|---|
| frontend | `DOWNSTREAM_URL` | `http://backend:8000` | where `/work` proxies to |
| frontend | `FAIL_READINESS` | `false` | `true` → `/readyz` always 500, `/healthz` stays healthy |
| frontend | `CRASH_AFTER_SECONDS` | unset | process exits non-zero after N seconds |
| backend | `DOWNSTREAM_URL` | `http://cache:8000` | where `/work` proxies to |
| backend | `FAIL_AFTER_N_REQUESTS` | unset | `/work` returns 500 after N successful calls |
| backend | `MEMORY_LEAK_MB_PER_SEC` | unset | background thread grows RSS by N MB/sec |
| cache | `SLOW_STARTUP_SECONDS` | `0` | delays `/healthz`/`/readyz` returning 200 by N seconds after start |
| cache | `CRASH_AFTER_SECONDS` | unset | process exits non-zero after N seconds |

All fault-mode vars are off by default (unset). Multiple vars on the same service are
additive.

## Build and load into kind

Each image builds and loads independently:

```bash
docker build -t test-app-frontend:local test_app/frontend/
docker build -t test-app-backend:local test_app/backend/
docker build -t test-app-cache:local test_app/cache/

kind load docker-image test-app-frontend:local
kind load docker-image test-app-backend:local
kind load docker-image test-app-cache:local
```

## Manual verification

See the per-service verification steps in
`docs/superpowers/plans/2026-07-14-test-app-microservice-chain.md` (Tasks 1-3) for the full
curl-based checklist covering default healthy behavior and every fault mode. No automated
pytest suite exists for `test_app/` — these are test fixtures, not application code under
this repo's own test coverage.

## Scope

No `cases/*` scenario directories, Kubernetes manifests, or `k8s_agent` integration tests
are included here — only the 3 microservices. Wiring the chain into an actual scenario is a
separate follow-up task.

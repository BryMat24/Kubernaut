# test_app

A small FastAPI service used to fake application-level failures in
`test/integration_test/k8s_agent/` scenarios (crashes, memory leaks, readiness failures,
unreachable dependencies) that stock images like `nginx`/`busybox` can't convincingly
produce. One image is reused across every scenario — behavior is selected entirely through
environment variables, all off by default.

## Endpoints

- `GET /healthz` — liveness probe target
- `GET /readyz` — readiness probe target
- `GET /work` — a normal request; also where request-count-based faults trigger

## Environment variables

| Env var | Effect |
|---|---|
| `CRASH_AFTER_SECONDS=N` | process exits non-zero after N seconds |
| `SLOW_STARTUP_SECONDS=N` | `/healthz` and `/readyz` return 503 until N seconds after start |
| `FAIL_READINESS=true` | `/readyz` always returns 500 |
| `MEMORY_LEAK_MB_PER_SEC=N` | background thread grows RSS by N MB every second |
| `FAIL_AFTER_N_REQUESTS=N` | `/work` starts returning 500 after N successful calls |
| `DEPENDENCY_URL=<url>` + `FAIL_DEPENDENCY=true` | `/work` and `/readyz` try to reach `DEPENDENCY_URL` and fail with a logged connection error |

Fault modes are additive — set as many as a scenario needs.

## Build and load into kind

kind cannot pull from a registry, so build locally and load the image directly into the
cluster's nodes:

```bash
docker build -t test-app:local test_app/
kind load docker-image test-app:local
```

Reference the image in a scenario manifest as `image: test-app:local` with
`imagePullPolicy: IfNotPresent` (or `Never`), since it only exists on the kind nodes, not in
a registry.

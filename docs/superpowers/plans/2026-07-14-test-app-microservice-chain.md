# Test App Microservice Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 3 separate FastAPI microservices (`frontend → backend → cache`) under `test_app/`, each with 1-2 env-var-toggled fault modes, wired as a real HTTP call chain so failures propagate naturally — a reusable fixture for future `k8s_agent` integration test scenarios that need application-level, cross-service failures.

**Architecture:** `frontend` calls `backend` on `/work`; `backend` calls `cache` on `/work`; `cache` is the terminal node. Each service exposes `/healthz`, `/readyz`, `/work`. `frontend`/`backend` read `DOWNSTREAM_URL` for where to call next.

**Tech Stack:** FastAPI + `uvicorn` on `python:3.12-slim`, `httpx` for outbound calls (frontend/backend only).

## Global Constraints

- Base image: `python:3.12-slim` (matches `mcp_servers/k8s_mcp_server/Dockerfile`).
- Each service reads `PORT` env var, default `8000`, binds `0.0.0.0` (matches `mcp_servers/k8s_mcp_server/server.py`'s convention).
- All fault-mode env vars default unset/off — default behavior is healthy.
- Manual verification only (curl-based) — no pytest added for `test_app/` itself.
- No `docker-compose`; kind is the real target. No new `cases/*` scenario directories, manifests, or `k8s_agent` integration tests — wiring into a scenario is a follow-up task, explicitly out of scope here.
- The old single-image `test_app/app.py` design (already deleted in the working tree) is not restored.

---

### Task 1: `cache` service (terminal node)

**Files:**
- Create: `test_app/cache/app.py`
- Create: `test_app/cache/requirements.txt`
- Create: `test_app/cache/Dockerfile`

**Interfaces:**
- Produces: HTTP service on port `8000` (env `PORT`) exposing `GET /healthz`, `GET /readyz`, `GET /work` (returns `{"service": "cache", "status": "ok"}`, HTTP 200).
- Produces env vars: `SLOW_STARTUP_SECONDS` (float, default `0`) delays `/healthz` and `/readyz` returning 200 (returns 503 until elapsed since process start), `CRASH_AFTER_SECONDS` (float, unset by default) exits the process non-zero after that many seconds.
- Consumes: nothing (terminal node, no outbound calls).

- [ ] **Step 1: Write `test_app/cache/app.py`**

```python
import os
import threading
import time

import uvicorn
from fastapi import FastAPI, Response

app = FastAPI()

START_TIME = time.time()
SLOW_STARTUP_SECONDS = float(os.environ.get("SLOW_STARTUP_SECONDS", "0"))
CRASH_AFTER_SECONDS = os.environ.get("CRASH_AFTER_SECONDS")


def _startup_complete() -> bool:
    return (time.time() - START_TIME) >= SLOW_STARTUP_SECONDS


def _crash_after(seconds: float) -> None:
    time.sleep(seconds)
    os._exit(1)


if CRASH_AFTER_SECONDS:
    threading.Thread(target=_crash_after, args=(float(CRASH_AFTER_SECONDS),), daemon=True).start()


@app.get("/healthz")
def healthz():
    if not _startup_complete():
        return Response(status_code=503, content="starting up")
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    if not _startup_complete():
        return Response(status_code=503, content="starting up")
    return {"status": "ready"}


@app.get("/work")
def work():
    return {"service": "cache", "status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Write `test_app/cache/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
```

- [ ] **Step 3: Write `test_app/cache/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
ENV PORT=8000

CMD ["python", "app.py"]
```

- [ ] **Step 4: Build the image**

Run: `docker build -t test-app-cache:local test_app/cache/`
Expected: build succeeds, ends with `naming to docker.io/library/test-app-cache:local`

- [ ] **Step 5: Verify default healthy behavior**

Run:
```bash
docker run -d --name test-cache -p 8002:8000 test-app-cache:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/healthz
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/readyz
curl -s http://localhost:8002/work
docker stop test-cache && docker rm test-cache
```
Expected: `200`, `200`, then `{"service":"cache","status":"ok"}`

- [ ] **Step 6: Verify `SLOW_STARTUP_SECONDS`**

Run:
```bash
docker run -d --name test-cache-slow -p 8002:8000 -e SLOW_STARTUP_SECONDS=5 test-app-cache:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/healthz
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/healthz
docker stop test-cache-slow && docker rm test-cache-slow
```
Expected: first curl `503`, second curl (after the 5s elapse) `200`

- [ ] **Step 7: Verify `CRASH_AFTER_SECONDS`**

Run:
```bash
docker run -d --name test-cache-crash -p 8002:8000 -e CRASH_AFTER_SECONDS=3 test-app-cache:local
sleep 5
docker ps -a --filter name=test-cache-crash --format "{{.Status}}"
docker rm test-cache-crash
```
Expected: status line contains `Exited (1)`

- [ ] **Step 8: Commit**

```bash
git add test_app/cache/
git commit -m "feat: add test_app cache service (terminal node)"
```

---

### Task 2: `backend` service (calls `cache`)

**Files:**
- Create: `test_app/backend/app.py`
- Create: `test_app/backend/requirements.txt`
- Create: `test_app/backend/Dockerfile`

**Interfaces:**
- Consumes: `cache` service's `GET /work` contract from Task 1 (returns 200 with JSON body on success; connection failure/timeout when `cache` is down or crashed).
- Produces: HTTP service on port `8000` (env `PORT`) exposing `GET /healthz` (always 200), `GET /readyz` (always 200), `GET /work` (proxies to `DOWNSTREAM_URL/work`, returns downstream's status/body, or HTTP 502 with an error message on connection failure).
- Produces env vars: `DOWNSTREAM_URL` (default `http://cache:8000`), `FAIL_AFTER_N_REQUESTS` (int, unset by default) makes `/work` return 500 after that many successful calls, `MEMORY_LEAK_MB_PER_SEC` (float, unset by default) grows RSS by that many MB every second via a background thread.

- [ ] **Step 1: Write `test_app/backend/app.py`**

```python
import os
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Response

app = FastAPI()

DOWNSTREAM_URL = os.environ.get("DOWNSTREAM_URL", "http://cache:8000")
FAIL_AFTER_N_REQUESTS = os.environ.get("FAIL_AFTER_N_REQUESTS")
MEMORY_LEAK_MB_PER_SEC = os.environ.get("MEMORY_LEAK_MB_PER_SEC")

_request_count = 0
_leak_buffer: list[bytearray] = []


def _leak(mb_per_sec: float) -> None:
    while True:
        _leak_buffer.append(bytearray(int(mb_per_sec * 1024 * 1024)))
        time.sleep(1)


if MEMORY_LEAK_MB_PER_SEC:
    threading.Thread(target=_leak, args=(float(MEMORY_LEAK_MB_PER_SEC),), daemon=True).start()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/work")
def work():
    global _request_count
    _request_count += 1

    if FAIL_AFTER_N_REQUESTS and _request_count > int(FAIL_AFTER_N_REQUESTS):
        return Response(status_code=500, content=f"failing after {FAIL_AFTER_N_REQUESTS} requests")

    try:
        resp = httpx.get(f"{DOWNSTREAM_URL}/work", timeout=5.0)
        return Response(status_code=resp.status_code, content=resp.content)
    except httpx.RequestError as exc:
        return Response(status_code=502, content=f"downstream error: {exc}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Write `test_app/backend/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
```

- [ ] **Step 3: Write `test_app/backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
ENV PORT=8000

CMD ["python", "app.py"]
```

- [ ] **Step 4: Build the image**

Run: `docker build -t test-app-backend:local test_app/backend/`
Expected: build succeeds, ends with `naming to docker.io/library/test-app-backend:local`

- [ ] **Step 5: Verify default healthy behavior against a real `cache`**

Run:
```bash
docker network create test-app-net
docker run -d --name cache --network test-app-net test-app-cache:local
docker run -d --name backend --network test-app-net -p 8001:8000 -e DOWNSTREAM_URL=http://cache:8000 test-app-backend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/healthz
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/readyz
curl -s http://localhost:8001/work
```
Expected: `200`, `200`, then `{"service":"cache","status":"ok"}` (proxied straight through from `cache`)

- [ ] **Step 6: Verify `FAIL_AFTER_N_REQUESTS`**

Run:
```bash
docker stop backend && docker rm backend
docker run -d --name backend --network test-app-net -p 8001:8000 -e DOWNSTREAM_URL=http://cache:8000 -e FAIL_AFTER_N_REQUESTS=2 test-app-backend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/work
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/work
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/work
docker stop backend && docker rm backend
```
Expected: `200`, `200`, `500` (fails on the 3rd call)

- [ ] **Step 7: Verify real downstream failure propagates (`cache` stopped → `backend` returns 502)**

Run:
```bash
docker stop cache
docker run -d --name backend --network test-app-net -p 8001:8000 -e DOWNSTREAM_URL=http://cache:8000 test-app-backend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/work
docker stop backend && docker rm backend && docker rm cache && docker network rm test-app-net
```
Expected: `502`

- [ ] **Step 8: Commit**

```bash
git add test_app/backend/
git commit -m "feat: add test_app backend service (calls cache)"
```

---

### Task 3: `frontend` service (calls `backend`)

**Files:**
- Create: `test_app/frontend/app.py`
- Create: `test_app/frontend/requirements.txt`
- Create: `test_app/frontend/Dockerfile`

**Interfaces:**
- Consumes: `backend` service's `GET /work` contract from Task 2 (returns downstream-proxied status/body on success; 502 or connection failure when `backend` is down/crashed).
- Produces: HTTP service on port `8000` (env `PORT`) exposing `GET /healthz` (always 200), `GET /readyz` (200 unless `FAIL_READINESS=true`, then 500), `GET /work` (proxies to `DOWNSTREAM_URL/work`, returns downstream's status/body, or HTTP 502 on connection failure).
- Produces env vars: `DOWNSTREAM_URL` (default `http://backend:8000`), `FAIL_READINESS` (bool string `"true"`/`"false"`, default `"false"`), `CRASH_AFTER_SECONDS` (float, unset by default) exits the process non-zero after that many seconds.

- [ ] **Step 1: Write `test_app/frontend/app.py`**

```python
import os
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Response

app = FastAPI()

DOWNSTREAM_URL = os.environ.get("DOWNSTREAM_URL", "http://backend:8000")
FAIL_READINESS = os.environ.get("FAIL_READINESS", "false").lower() == "true"
CRASH_AFTER_SECONDS = os.environ.get("CRASH_AFTER_SECONDS")


def _crash_after(seconds: float) -> None:
    time.sleep(seconds)
    os._exit(1)


if CRASH_AFTER_SECONDS:
    threading.Thread(target=_crash_after, args=(float(CRASH_AFTER_SECONDS),), daemon=True).start()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    if FAIL_READINESS:
        return Response(status_code=500, content="not ready")
    return {"status": "ready"}


@app.get("/work")
def work():
    try:
        resp = httpx.get(f"{DOWNSTREAM_URL}/work", timeout=5.0)
        return Response(status_code=resp.status_code, content=resp.content)
    except httpx.RequestError as exc:
        return Response(status_code=502, content=f"downstream error: {exc}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Write `test_app/frontend/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
```

- [ ] **Step 3: Write `test_app/frontend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
ENV PORT=8000

CMD ["python", "app.py"]
```

- [ ] **Step 4: Build the image**

Run: `docker build -t test-app-frontend:local test_app/frontend/`
Expected: build succeeds, ends with `naming to docker.io/library/test-app-frontend:local`

- [ ] **Step 5: Verify default healthy behavior and `FAIL_READINESS` against a real `backend`**

Run:
```bash
docker network create test-app-net
docker run -d --name cache --network test-app-net test-app-cache:local
docker run -d --name backend --network test-app-net -e DOWNSTREAM_URL=http://cache:8000 test-app-backend:local
docker run -d --name frontend --network test-app-net -p 8000:8000 -e DOWNSTREAM_URL=http://backend:8000 test-app-frontend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/readyz
curl -s http://localhost:8000/work
docker stop frontend && docker rm frontend
docker run -d --name frontend --network test-app-net -p 8000:8000 -e DOWNSTREAM_URL=http://backend:8000 -e FAIL_READINESS=true test-app-frontend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/readyz
```
Expected: `200`, `200`, `{"service":"cache","status":"ok"}` (proxied through both hops), then `500` for the `FAIL_READINESS=true` run

- [ ] **Step 6: Verify real downstream failure propagates (`backend` stopped → `frontend` returns 502)**

Run:
```bash
docker stop frontend && docker rm frontend
docker stop backend
docker run -d --name frontend --network test-app-net -p 8000:8000 -e DOWNSTREAM_URL=http://backend:8000 test-app-frontend:local
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/work
docker stop frontend && docker rm frontend && docker rm backend && docker rm cache && docker network rm test-app-net
```
Expected: `502`

- [ ] **Step 7: Commit**

```bash
git add test_app/frontend/
git commit -m "feat: add test_app frontend service (calls backend)"
```

---

### Task 4: Shared README

**Files:**
- Create: `test_app/README.md`

**Interfaces:**
- Consumes: env var tables and endpoint contracts produced by Tasks 1-3 (no code interfaces — this is documentation only).

- [ ] **Step 1: Write `test_app/README.md`**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add test_app/README.md
git commit -m "docs: add test_app README with env var reference and build instructions"
```

---

### Task 5: End-to-end chain verification (validates real fault propagation)

**Files:**
- None created or modified — this task is pure verification of Tasks 1-3's already-committed images, confirming the core design goal (real cascading failures) works across all 3 hops together, not just one hop at a time as verified in each task above.

**Interfaces:**
- Consumes: `test-app-frontend:local`, `test-app-backend:local`, `test-app-cache:local` images from Tasks 1-3, and the same `DOWNSTREAM_URL` env var contract each already implements.

- [ ] **Step 1: Bring up the full healthy chain**

Run:
```bash
docker network create test-app-net
docker run -d --name cache --network test-app-net test-app-cache:local
docker run -d --name backend --network test-app-net -e DOWNSTREAM_URL=http://cache:8000 test-app-backend:local
docker run -d --name frontend --network test-app-net -p 8000:8000 -e DOWNSTREAM_URL=http://backend:8000 test-app-frontend:local
sleep 1
curl -s http://localhost:8000/work
```
Expected: `{"service":"cache","status":"ok"}` — confirms all 3 hops proxy correctly end-to-end

- [ ] **Step 2: Trigger a real cascading failure — crash `cache`, confirm it surfaces at `frontend`**

Run:
```bash
docker exec cache kill 1
sleep 1
curl -s -o /dev/null -w "backend: %{http_code}\n" http://localhost:8000/work
docker logs frontend --tail 5
```
Expected: `backend: 502` — `cache`'s process is gone, `backend`'s call to it fails with a
connection error, `backend` returns 502, `frontend` proxies that 502 straight through.
This confirms the design's central claim: the failure is real and propagates through real
HTTP calls, not a simulated flag.

- [ ] **Step 3: Clean up**

Run:
```bash
docker stop frontend backend && docker rm frontend backend cache && docker network rm test-app-net
```
Expected: all containers and the network removed, no leftover state

- [ ] **Step 4: Update `test_app/README.md` to note this was verified**

Add a line under "Manual verification" confirming the 3-hop cascading-failure check was run:

```markdown
The full 3-hop cascading-failure check (crashing `cache` and confirming the failure surfaces
as a 502 at `frontend`) has been verified manually per this README's build instructions.
```

- [ ] **Step 5: Commit**

```bash
git add test_app/README.md
git commit -m "docs: note end-to-end cascading-failure verification in test_app README"
```

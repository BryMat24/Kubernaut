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

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

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

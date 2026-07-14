import os
import threading
import time

import httpx
from fastapi import FastAPI, Response

CRASH_AFTER_SECONDS = int(os.environ.get("CRASH_AFTER_SECONDS", "0"))
SLOW_STARTUP_SECONDS = int(os.environ.get("SLOW_STARTUP_SECONDS", "0"))
FAIL_READINESS = os.environ.get("FAIL_READINESS", "false").lower() == "true"
MEMORY_LEAK_MB_PER_SEC = int(os.environ.get("MEMORY_LEAK_MB_PER_SEC", "0"))
FAIL_AFTER_N_REQUESTS = int(os.environ.get("FAIL_AFTER_N_REQUESTS", "0"))
DEPENDENCY_URL = os.environ.get("DEPENDENCY_URL", "")
FAIL_DEPENDENCY = os.environ.get("FAIL_DEPENDENCY", "false").lower() == "true"

_start_time = time.monotonic()
_work_request_count = 0
_leaked_memory: list[bytes] = []

app = FastAPI()


def _crash_after_delay() -> None:
    time.sleep(CRASH_AFTER_SECONDS)
    print(f"CRASH_AFTER_SECONDS={CRASH_AFTER_SECONDS} elapsed, exiting non-zero", flush=True)
    # sys.exit() only raises SystemExit in this thread, not the process --
    # os._exit() is required to actually terminate the whole container.
    os._exit(1)


def _leak_memory_forever() -> None:
    while True:
        _leaked_memory.append(b"x" * (MEMORY_LEAK_MB_PER_SEC * 1024 * 1024))
        time.sleep(1)


if CRASH_AFTER_SECONDS > 0:
    threading.Thread(target=_crash_after_delay, daemon=True).start()

if MEMORY_LEAK_MB_PER_SEC > 0:
    threading.Thread(target=_leak_memory_forever, daemon=True).start()


def _still_starting_up() -> bool:
    return (time.monotonic() - _start_time) < SLOW_STARTUP_SECONDS


def _check_dependency() -> bool:
    if not (FAIL_DEPENDENCY and DEPENDENCY_URL):
        return True
    try:
        httpx.get(DEPENDENCY_URL, timeout=2.0)
        return True
    except httpx.HTTPError as exc:
        print(f"dependency check failed: {exc}", flush=True)
        return False


@app.get("/healthz")
def healthz(response: Response):
    if _still_starting_up():
        response.status_code = 503
        return {"status": "starting"}
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response):
    if _still_starting_up():
        response.status_code = 503
        return {"status": "starting"}
    if FAIL_READINESS:
        response.status_code = 500
        return {"status": "not ready"}
    if not _check_dependency():
        response.status_code = 500
        return {"status": "dependency unreachable"}
    return {"status": "ok"}


@app.get("/work")
def work(response: Response):
    global _work_request_count
    _work_request_count += 1

    if not _check_dependency():
        response.status_code = 500
        return {"status": "error", "reason": "dependency unreachable"}

    if FAIL_AFTER_N_REQUESTS > 0 and _work_request_count > FAIL_AFTER_N_REQUESTS:
        response.status_code = 500
        return {"status": "error", "reason": "simulated failure after N requests"}

    return {"status": "ok", "request_count": _work_request_count}

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_background_job(worker_fn: Callable[[Callable[[Any], None]], Any]):
    """Run worker in a daemon thread immediately (do not wait for SSE client).

    Previously the worker started only when the ASGI server began iterating the
    generator. If the client aborted before that, ``try_begin_job`` could leave
    ``job_running`` stuck True forever (until process exit).
    """
    progress_events: list = []
    result_box: list = []
    error_box: list[str] = []
    done = threading.Event()

    def _emit(payload) -> None:
        if isinstance(payload, dict):
            if "type" not in payload:
                payload = {**payload, "type": "progress"}
            progress_events.append(payload)
        else:
            progress_events.append({"type": "progress", "message": str(payload)})

    def worker() -> None:
        try:
            result_box.append(worker_fn(_emit))
        except Exception as exc:  # noqa: BLE001
            error_box.append(str(exc))
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="sse-job", daemon=True)
    thread.start()

    def event_stream():
        last_i = 0
        while not done.is_set() or last_i < len(progress_events):
            while last_i < len(progress_events):
                payload = progress_events[last_i]
                last_i += 1
                yield _sse(
                    payload
                    if isinstance(payload, dict)
                    else {"type": "progress", "message": str(payload)}
                )
            if done.is_set() and last_i >= len(progress_events):
                break
            time.sleep(0.12)
        thread.join(timeout=1)

        if error_box:
            yield _sse({"type": "error", "message": error_box[0]})
            return
        yield _sse(result_box[0] if result_box else {"type": "done"})

    return event_stream()

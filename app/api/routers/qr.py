from __future__ import annotations

import base64
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import MessageOut, QrStartOut
from app.api.state import QrRuntime

router = APIRouter(tags=["qr"])


@router.post("/api/qr/start", response_model=QrStartOut)
def qr_start(request: Request) -> QrStartOut:
    state = request.app.state.api
    if state.qr is not None:
        state.qr.cancel.set()
    session = state.devices.start_qr_session()
    state.qr = QrRuntime(session=session, cancel=threading.Event())
    png = base64.b64encode(session.png_bytes()).decode("ascii")
    return QrStartOut(png_base64=png, service_name=session.service_name)


@router.post("/api/qr/cancel", response_model=MessageOut)
def qr_cancel(request: Request) -> MessageOut:
    state = request.app.state.api
    if state.qr is not None:
        state.qr.cancel.set()
        state.qr = None
    return MessageOut(message="QR отменён")


@router.get("/api/qr/wait")
def qr_wait(request: Request) -> StreamingResponse:
    """SSE: progress lines, then final event with ok/message."""
    state = request.app.state.api
    runtime = state.qr
    if runtime is None:
        raise HTTPException(status_code=400, detail="Сначала /api/qr/start")

    session = runtime.session
    cancel = runtime.cancel

    def event_stream():
        def progress(msg: str) -> None:
            # Collected via queue in thread — use list append from callback
            events.append(("progress", msg))

        events: list[tuple[str, str]] = []
        result_box: list = []

        def worker() -> None:
            result = state.devices.complete_qr_session(
                session,
                timeout=90.0,
                cancel_event=cancel,
                progress=lambda m: events.append(("progress", m)),
            )
            result_box.append(result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        import json
        import time

        last_i = 0
        while thread.is_alive() or last_i < len(events):
            while last_i < len(events):
                kind, msg = events[last_i]
                last_i += 1
                payload = json.dumps({"type": kind, "message": msg}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            time.sleep(0.15)

        thread.join(timeout=1)
        if result_box:
            result = result_box[0]
            payload = json.dumps(
                {
                    "type": "done",
                    "ok": result.success,
                    "message": result.message,
                    "host_port": result.connected_host_port or "",
                },
                ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"
        state.qr = None

    return StreamingResponse(event_stream(), media_type="text/event-stream")

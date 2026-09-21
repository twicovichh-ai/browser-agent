"""Web UI: chat with the agent on the left, the live browser on the right.

    python -m webapp.server            # http://localhost:8080

Env:
    ACCESS_KEY      if set, the page requires ?key=<ACCESS_KEY> once (then a cookie)
    PROFILE_DIR     Chrome profile dir (logins persist here)
    MAX_TASK_COST   $ limit per task (agent stops when reached)
"""

from __future__ import annotations

import asyncio
import hmac
import os
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .chrome import CDP_URL, launch_chrome
from .viewer import Viewer
from .worker import AgentWorker

STATIC = Path(__file__).parent / "static"
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
COOKIE = "agent_key"
PORT = int(os.getenv("PORT", "8080"))
DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"

app = FastAPI(title="Browser Agent")
# Public, harmless test shop: the agent's own Chrome opens it without the access cookie.
app.mount("/demo", StaticFiles(directory=DEMO_DIR), name="demo")
clients: set[WebSocket] = set()
history: deque[dict] = deque(maxlen=400)  # replayed to a newly opened page
state: dict = {"frame": None, "loop": None, "worker": None, "viewer": None}


def authorized(cookie: str | None) -> bool:
    return not ACCESS_KEY or (cookie is not None and hmac.compare_digest(cookie, ACCESS_KEY))


# ---------- thread -> asyncio bridge ----------

def emit(event: dict) -> None:
    """Called from the agent/viewer threads."""
    loop = state["loop"]
    if loop:
        loop.call_soon_threadsafe(_dispatch, event)


def _dispatch(event: dict) -> None:
    if event["type"] == "frame":
        state["frame"] = event  # only the latest frame matters; a sender task pushes it
        return
    history.append(event)
    for ws in list(clients):
        asyncio.create_task(_send(ws, event))


async def _send(ws: WebSocket, event: dict) -> None:
    try:
        await ws.send_json(event)
    except Exception:  # noqa: BLE001 — dead socket, drop it
        clients.discard(ws)


async def frame_pump() -> None:
    last = None
    while True:
        await asyncio.sleep(0.1)  # <= 10 fps to the page
        frame = state["frame"]
        if frame is not None and frame is not last:
            last = frame
            await asyncio.gather(*(_send(ws, frame) for ws in list(clients)))


@app.on_event("startup")
async def startup() -> None:
    state["loop"] = asyncio.get_running_loop()
    profile = os.getenv("PROFILE_DIR", str(Path(__file__).resolve().parent.parent / ".profile-web"))
    await asyncio.to_thread(launch_chrome, profile)
    worker = AgentWorker(CDP_URL, emit, demo_url=f"http://127.0.0.1:{PORT}/demo/shop.html")
    viewer = Viewer(CDP_URL, emit, active_url=lambda: worker.active_url)
    state.update(worker=worker, viewer=viewer)
    worker.start()
    viewer.start()
    asyncio.create_task(frame_pump())


# ---------- HTTP ----------

@app.get("/", response_model=None)
async def index(request: Request, key: str | None = None):
    if key and ACCESS_KEY and hmac.compare_digest(key, ACCESS_KEY):
        resp = RedirectResponse("/")
        resp.set_cookie(COOKIE, key, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=60 * 60 * 24 * 30)
        return resp
    if not authorized(request.cookies.get(COOKIE)):
        return HTMLResponse("<h3>Нужна ссылка с ключом доступа.</h3>", status_code=401)
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    w = state["worker"]
    return {"ok": True, "busy": bool(w and w.busy)}


# ---------- WebSocket ----------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not authorized(ws.cookies.get(COOKIE)):
        await ws.close(code=4401)
        return
    await ws.accept()
    clients.add(ws)
    worker: AgentWorker = state["worker"]
    viewer: Viewer = state["viewer"]
    for ev in list(history):
        await ws.send_json(ev)
    await ws.send_json({"type": "busy", "busy": worker.busy})
    if state["frame"]:
        await ws.send_json(state["frame"])
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "task":
                text = str(msg.get("text", "")).strip()[:2000]
                if not text:
                    continue
                if worker.busy:
                    await ws.send_json({"type": "error", "text": "Агент уже выполняет задачу. Дождитесь или нажмите «Стоп»."})
                    continue
                emit({"type": "user", "text": text})
                worker.tasks.put(text)
            elif kind == "answer":
                worker.ui.reply(int(msg["id"]), str(msg.get("text", ""))[:2000])
            elif kind == "confirm":
                worker.ui.reply(int(msg["id"]), bool(msg.get("ok")))
            elif kind == "stop":
                worker.stop_task()
            elif kind == "input" and msg.get("kind") in {"click", "wheel", "text", "key"}:
                viewer.inputs.put(msg)
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=PORT)

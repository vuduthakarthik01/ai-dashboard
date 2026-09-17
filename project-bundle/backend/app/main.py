import asyncio
import logging

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.realtime import hub
from app.routers import auth, chat, inventory, maintenance, plant, quality, safety
from app.security import decode_token

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("industrialmind")

app = FastAPI(title="IndustrialMind AI API", version="1.0.0",
              description="Autonomous AI factory manager for small manufacturers.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for module in (auth, plant, maintenance, inventory, safety, quality, chat):
    app.include_router(module.router)


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


@app.websocket("/ws")
async def stream(ws: WebSocket, token: str = Query(...)):
    """Live plant feed. One socket per signed-in client, joined to its plant room.

    Frames look like {"channel": "telemetry"|"alert"|"detection"|"energy"|
    "production"|"alert_update"|"work_order", "data": {...}}.
    """
    try:
        claims = decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    plant_id = claims["plant"]
    await hub.join(plant_id, ws)
    try:
        while True:
            await ws.receive_text()          # client heartbeats; server pushes
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(plant_id, ws)


@app.on_event("startup")
async def startup():
    await hub.start()
    if settings.simulate:
        from app.simulator import run_simulation
        app.state.sim = asyncio.create_task(run_simulation())
        log.info("Simulator started - set SIMULATE=false to read real equipment instead.")


@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "sim", None)
    if task:
        task.cancel()
    await hub.stop()

"""WebSocket fan-out.

One hub per process; Redis pub/sub carries events between processes so the
API can run behind more than one uvicorn worker without a client missing a
frame. Clients subscribe per plant and per channel (telemetry, alerts,
detections, production, energy).
"""
import asyncio
import json
import logging
from collections import defaultdict

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.config import settings

log = logging.getLogger("realtime")


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._redis: aioredis.Redis | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._redis:
            await self._redis.aclose()

    async def _pump(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("plant:*")
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            await self._local(message["channel"], message["data"])

    async def _local(self, room: str, payload: str) -> None:
        dead = []
        for ws in list(self._rooms.get(room, ())):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._rooms[room].discard(ws)

    async def join(self, plant_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms[f"plant:{plant_id}"].add(ws)

    def leave(self, plant_id: str, ws: WebSocket) -> None:
        self._rooms[f"plant:{plant_id}"].discard(ws)

    async def publish(self, plant_id: str, channel: str, data: dict) -> None:
        payload = json.dumps({"channel": channel, "data": data}, default=str)
        room = f"plant:{plant_id}"
        if self._redis:
            await self._redis.publish(room, payload)
        else:
            await self._local(room, payload)


hub = Hub()

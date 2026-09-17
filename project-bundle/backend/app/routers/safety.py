"""CCTV detections and the plant-wide alert stream."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Alert, Camera, Detection, Severity, User
from app.realtime import hub
from app.security import requires

router = APIRouter(prefix="/api", tags=["safety"])


@router.get("/cameras")
async def cameras(user: User = Depends(requires("view.cctv")),
                  db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Camera).where(Camera.plant_id == user.plant_id))).scalars().all()
    return [{"code": c.code, "zone": c.zone, "online": c.online,
             "models": c.models_enabled,
             "stream": f"/api/cameras/{c.code}/stream.m3u8"} for c in rows]


@router.get("/detections")
async def detections(hours: int = Query(24, le=168),
                     user: User = Depends(requires("view.cctv")),
                     db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await db.execute(
        select(Detection, Camera.code, Camera.zone)
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Camera.plant_id == user.plant_id, Detection.time >= since)
        .order_by(Detection.time.desc()).limit(200))).all()
    return [{"at": d.time, "camera": code, "zone": zone, "kind": d.kind,
             "confidence": d.confidence, "bbox": d.bbox, "clip": d.clip_url}
            for d, code, zone in rows]


@router.get("/alerts")
async def alerts(module: str | None = None, severity: Severity | None = None,
                 open_only: bool = True, limit: int = Query(50, le=200),
                 user: User = Depends(requires("view.alerts")),
                 db: AsyncSession = Depends(get_db)):
    q = select(Alert).where(Alert.plant_id == user.plant_id)
    if module:
        q = q.where(Alert.module == module)
    if severity:
        q = q.where(Alert.severity == severity)
    if open_only:
        q = q.where(Alert.resolved_at.is_(None))
    rows = (await db.execute(q.order_by(Alert.created_at.desc()).limit(limit))).scalars()
    return [{"id": str(a.id), "severity": a.severity.value, "module": a.module,
             "title": a.title, "detail": a.detail, "context": a.context,
             "created_at": a.created_at, "acknowledged": a.acknowledged_at is not None,
             "resolved": a.resolved_at is not None} for a in rows]


async def _transition(alert_id: str, user: User, db: AsyncSession, resolve: bool):
    alert = (await db.execute(select(Alert).where(
        Alert.id == alert_id, Alert.plant_id == user.plant_id))).scalar_one_or_none()
    if alert is None:
        raise HTTPException(404, "That alert is not in this plant.")
    now = datetime.now(timezone.utc)
    alert.acknowledged_by, alert.acknowledged_at = user.id, alert.acknowledged_at or now
    if resolve:
        alert.resolved_at = now
    await db.commit()
    await hub.publish(str(user.plant_id), "alert_update",
                      {"id": str(alert.id), "acknowledged": True, "resolved": resolve,
                       "by": user.name})
    return {"id": str(alert.id), "acknowledged": True, "resolved": resolve}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: str, user: User = Depends(requires("alert.ack")),
                      db: AsyncSession = Depends(get_db)):
    return await _transition(alert_id, user, db, resolve=False)


@router.post("/alerts/{alert_id}/resolve")
async def resolve(alert_id: str, user: User = Depends(requires("alert.resolve")),
                  db: AsyncSession = Depends(get_db)):
    return await _transition(alert_id, user, db, resolve=True)

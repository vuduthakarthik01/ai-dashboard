from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Machine, QualityInspection, User
from app.security import requires

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/summary")
async def summary(hours: int = Query(24, le=720),
                  user: User = Depends(requires("view.quality")),
                  db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    ids = select(Machine.id).where(Machine.plant_id == user.plant_id).scalar_subquery()

    passed, failed = (await db.execute(
        select(func.count().filter(QualityInspection.passed.is_(True)),
               func.count().filter(QualityInspection.passed.is_(False)))
        .where(QualityInspection.machine_id.in_(ids),
               QualityInspection.time >= since))).one()

    defects = (await db.execute(
        select(QualityInspection.defect, func.count())
        .where(QualityInspection.machine_id.in_(ids), QualityInspection.time >= since,
               QualityInspection.passed.is_(False))
        .group_by(QualityInspection.defect).order_by(func.count().desc()))).all()

    by_machine = (await db.execute(
        select(Machine.code, func.count())
        .join(QualityInspection, QualityInspection.machine_id == Machine.id)
        .where(Machine.plant_id == user.plant_id, QualityInspection.time >= since,
               QualityInspection.passed.is_(False))
        .group_by(Machine.code))).all()

    total = passed + failed
    return {"inspected": total, "passed": passed, "failed": failed,
            "reject_rate_pct": round(failed / max(total, 1) * 100, 2),
            "defects": [{"defect": d, "count": c} for d, c in defects],
            "rejects_by_machine": [{"code": m, "count": c} for m, c in by_machine]}


@router.get("/recent")
async def recent(limit: int = Query(30, le=200),
                 user: User = Depends(requires("view.quality")),
                 db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(QualityInspection, Machine.code)
        .join(Machine, Machine.id == QualityInspection.machine_id)
        .where(Machine.plant_id == user.plant_id, QualityInspection.passed.is_(False))
        .order_by(QualityInspection.time.desc()).limit(limit))).all()
    return [{"at": q.time, "batch": q.batch, "machine": code, "defect": q.defect,
             "confidence": q.confidence, "image": q.image_url} for q, code in rows]

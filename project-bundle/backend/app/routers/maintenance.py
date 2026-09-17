from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Machine, Telemetry, User, WorkOrder
from app.realtime import hub
from app.security import requires
from app.services import predictive

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


class WorkOrderIn(BaseModel):
    machine: str
    reason: str
    priority: str = "Normal"


@router.get("/health")
async def health(user: User = Depends(requires("view.machines")),
                 db: AsyncSession = Depends(get_db)):
    machines = (await db.execute(
        select(Machine).where(Machine.plant_id == user.plant_id))).scalars().all()
    out = []
    for m in machines:
        last = (await db.execute(
            select(Telemetry).where(Telemetry.machine_id == m.id)
            .order_by(Telemetry.time.desc()).limit(1))).scalar_one_or_none()
        result = predictive.score(
            m.hours_since_service, m.service_interval_hours,
            last.temperature_c if last else 40, last.vibration_mm_s if last else 1.0)
        out.append({"code": m.code, "name": m.name, "status": m.status,
                    "health": result.health, "rul_days": result.rul_days,
                    "contributions": result.contributions,
                    "temperature_c": last.temperature_c if last else None,
                    "vibration_mm_s": last.vibration_mm_s if last else None})
    return sorted(out, key=lambda r: r["health"])


@router.get("/work-orders")
async def list_work_orders(user: User = Depends(requires("view.machines")),
                           db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(WorkOrder, Machine.code).join(Machine, Machine.id == WorkOrder.machine_id)
        .where(WorkOrder.plant_id == user.plant_id)
        .order_by(WorkOrder.created_at.desc()).limit(100))).all()
    return [{"id": str(w.id), "machine": code, "reason": w.reason, "priority": w.priority,
             "status": w.status, "auto": w.auto_raised, "created_at": w.created_at}
            for w, code in rows]


@router.post("/work-orders", status_code=201)
async def create_work_order(body: WorkOrderIn,
                            user: User = Depends(requires("wo.create")),
                            db: AsyncSession = Depends(get_db)):
    machine = (await db.execute(select(Machine).where(
        Machine.plant_id == user.plant_id, Machine.code == body.machine))).scalar_one_or_none()
    if machine is None:
        raise HTTPException(404, f"No machine with code {body.machine} in this plant.")
    wo = WorkOrder(plant_id=user.plant_id, machine_id=machine.id, reason=body.reason,
                   priority=body.priority, created_by=user.id,
                   due_at=datetime.now(timezone.utc) + timedelta(days=2))
    db.add(wo)
    await db.commit()
    await hub.publish(str(user.plant_id), "work_order",
                      {"id": str(wo.id), "machine": machine.code, "status": "Open"})
    return {"id": str(wo.id), "machine": machine.code, "status": wo.status}


@router.post("/work-orders/{wo_id}/close")
async def close_work_order(wo_id: str, user: User = Depends(requires("wo.close")),
                           db: AsyncSession = Depends(get_db)):
    wo = (await db.execute(select(WorkOrder).where(
        WorkOrder.id == wo_id, WorkOrder.plant_id == user.plant_id))).scalar_one_or_none()
    if wo is None:
        raise HTTPException(404, "That work order is not in this plant.")
    wo.status, wo.closed_at = "Closed", datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(wo.id), "status": wo.status}

"""Machines, production and energy - the operating picture."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EnergyReading, Machine, QualityInspection, Telemetry, User
from app.security import requires

router = APIRouter(prefix="/api", tags=["plant"])

TARIFF = [(0, 6, 5.10, "Off-peak"), (6, 9, 7.40, "Normal"), (9, 12, 9.85, "Peak"),
          (12, 18, 7.40, "Normal"), (18, 22, 9.85, "Peak"), (22, 24, 5.10, "Off-peak")]


def tariff_for(hour: int):
    for lo, hi, rate, band in TARIFF:
        if lo <= hour < hi:
            return rate, band
    return 7.40, "Normal"


@router.get("/machines")
async def machines(user: User = Depends(requires("view.machines")),
                   db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Machine).where(Machine.plant_id == user.plant_id).order_by(Machine.code))).scalars()
    return [{"code": m.code, "name": m.name, "cell": m.cell, "status": m.status,
             "health": m.health, "rul_days": m.rul_days,
             "rated_per_hour": m.rated_per_hour,
             "hours_since_service": round(m.hours_since_service, 1),
             "service_interval_hours": m.service_interval_hours} for m in rows]


@router.get("/machines/{code}/telemetry")
async def telemetry(code: str, minutes: int = Query(60, le=1440),
                    user: User = Depends(requires("view.machines")),
                    db: AsyncSession = Depends(get_db)):
    machine = (await db.execute(select(Machine).where(
        Machine.plant_id == user.plant_id, Machine.code == code))).scalar_one()
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (await db.execute(
        select(Telemetry).where(Telemetry.machine_id == machine.id, Telemetry.time >= since)
        .order_by(Telemetry.time))).scalars()
    return [{"t": r.time, "temperature_c": r.temperature_c, "vibration_mm_s": r.vibration_mm_s,
             "load_pct": r.load_pct, "power_kw": r.power_kw} for r in rows]


@router.get("/production/summary")
async def production(user: User = Depends(requires("view.production")),
                     db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    machines = (await db.execute(
        select(Machine).where(Machine.plant_id == user.plant_id))).scalars().all()
    ids = [m.id for m in machines]

    produced = (await db.execute(
        select(Telemetry.machine_id, func.sum(Telemetry.units_made))
        .where(Telemetry.machine_id.in_(ids), Telemetry.time >= since)
        .group_by(Telemetry.machine_id))).all()
    by_machine = {mid: float(total or 0) for mid, total in produced}

    passed, failed = (await db.execute(
        select(func.count().filter(QualityInspection.passed.is_(True)),
               func.count().filter(QualityInspection.passed.is_(False)))
        .where(QualityInspection.machine_id.in_(ids),
               QualityInspection.time >= since))).one()

    running = sum(1 for m in machines if m.status != "fault")
    availability = running / max(len(machines), 1)
    capacity = sum(m.rated_per_hour for m in machines) * max(
        1, (datetime.now(timezone.utc) - since).total_seconds() / 3600)
    output = sum(by_machine.values())
    performance = min(1.0, output / max(capacity, 1))
    quality = passed / max(passed + failed, 1)

    return {"availability": round(availability * 100, 1),
            "performance": round(performance * 100, 1),
            "quality": round(quality * 100, 1),
            "oee": round(availability * performance * quality * 100, 1),
            "units_today": round(output),
            "by_machine": [{"code": m.code, "units": round(by_machine.get(m.id, 0)),
                            "status": m.status} for m in machines]}


@router.get("/energy/summary")
async def energy(user: User = Depends(requires("view.energy")),
                 db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    kwh, cost, peak = (await db.execute(
        select(func.sum(EnergyReading.energy_kwh), func.sum(EnergyReading.cost_inr),
               func.max(EnergyReading.demand_kw))
        .where(EnergyReading.plant_id == user.plant_id,
               EnergyReading.time >= since))).one()
    latest = (await db.execute(
        select(EnergyReading).where(EnergyReading.plant_id == user.plant_id)
        .order_by(EnergyReading.time.desc()).limit(1))).scalar_one_or_none()
    rate, band = tariff_for(datetime.now(timezone.utc).hour)
    return {"demand_kw": round(latest.demand_kw if latest else 0, 1),
            "kwh_today": round(kwh or 0, 1), "cost_today_inr": round(cost or 0),
            "peak_kw_today": round(peak or 0, 1), "tariff_band": band, "rate_per_kwh": rate}

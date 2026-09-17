"""Equipment simulator.

Stands in for the edge gateway so the whole stack can be run without a factory
attached. It writes to the same tables and publishes to the same hub channels
the real ingest path uses, which means every screen, alert rule and assistant
tool is exercised by it. Set SIMULATE=false and point the MQTT/OPC-UA ingest at
real PLC tags; nothing downstream changes.
"""
import asyncio
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (Alert, EnergyReading, Machine, QualityInspection, Severity,
                        Telemetry, WorkOrder)
from app.realtime import hub
from app.routers.plant import tariff_for
from app.services import predictive

DEFECTS = ["Surface scratch", "Dimension out of tolerance", "Short mould",
           "Burr on edge", "Coating thin", "Weld porosity"]
TICK_SECONDS = 2


async def run_simulation() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:                      # a simulator fault must not kill the API
            await asyncio.sleep(5)
        await asyncio.sleep(TICK_SECONDS)


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    day_factor = 1.0 if 9 <= now.hour < 18 else (0.6 if 18 <= now.hour < 22 else 0.25)

    async with SessionLocal() as db:
        machines = (await db.execute(select(Machine))).scalars().all()
        if not machines:
            return
        total_kw = 9.4                          # lighting, office, pumps

        for m in machines:
            state = m.meta or {}
            temp = state.get("temp", random.uniform(42, 58))
            vib = state.get("vib", random.uniform(1.1, 2.2))

            if m.status == "fault":
                if random.random() < 0.05:
                    m.status, m.hours_since_service = "run", 0.0
                    temp, vib = random.uniform(45, 58), random.uniform(1.1, 2.0)
                load = 0.0
            else:
                if random.random() < (0.009 if m.health < 55 else 0.0016) * day_factor:
                    m.status = "fault"
                    await _alert(db, m, "critical", f"{m.code} stopped",
                                 f"Tripped at {vib:.1f} mm/s and {temp:.0f} C.")
                    db.add(WorkOrder(plant_id=m.plant_id, machine_id=m.id,
                                     reason=f"Unplanned stop at {temp:.0f} C", priority="High",
                                     auto_raised=True))
                m.hours_since_service += TICK_SECONDS / 3600
                drift = (1 - m.health / 100) * 0.09
                temp = min(118, max(34, temp + random.uniform(-0.9, 0.9) + drift * day_factor))
                vib = min(9.5, max(0.4, vib + random.uniform(-0.12, 0.12) + drift * 0.09))
                load = min(99, max(30, state.get("load", 70) + random.uniform(-4, 4))) * (
                    1 if day_factor > 0.5 else 0.7)

            power = m.rated_kw * (0.28 + 0.72 * load / 100) if m.status != "fault" else 0.0
            units = (m.rated_per_hour / 3600 * TICK_SECONDS * (load / 100) * day_factor
                     if m.status != "fault" else 0.0)
            total_kw += power

            result = predictive.score(m.hours_since_service, m.service_interval_hours, temp, vib)
            m.health, m.rul_days = result.health, result.rul_days
            m.meta = {"temp": temp, "vib": vib, "load": load}
            if m.status != "fault":
                m.status = "watch" if m.health < 58 else ("idle" if load < 35 else "run")

            db.add(Telemetry(time=now, machine_id=m.id, temperature_c=temp,
                             vibration_mm_s=vib, load_pct=load,
                             current_a=m.rated_kw * 1.9 * load / 100, rpm=load * 24,
                             power_kw=power, units_made=units))
            await hub.publish(str(m.plant_id), "telemetry",
                              {"machine": m.code, "status": m.status, "health": m.health,
                               "temperature_c": round(temp, 1), "vibration_mm_s": round(vib, 2),
                               "load_pct": round(load), "power_kw": round(power, 1)})

            raised = predictive.should_raise_alert(result, temp, vib)
            if raised and random.random() < 0.15:
                await _alert(db, m, raised[0], f"{m.code}: {raised[1]}", raised[2])

            if m.status != "fault" and random.random() < 0.05 * day_factor:
                failed = random.random() < (0.12 if m.status == "watch" else 0.045)
                db.add(QualityInspection(
                    time=now, machine_id=m.id,
                    batch="B-" + uuid.uuid4().hex[:6].upper(), passed=not failed,
                    defect=None if not failed else random.choice(DEFECTS),
                    confidence=random.uniform(0.86, 0.99)))

        rate, band = tariff_for(now.hour)
        kwh = total_kw * TICK_SECONDS / 3600
        db.add(EnergyReading(time=now, plant_id=machines[0].plant_id, demand_kw=total_kw,
                             energy_kwh=kwh, tariff_band=band, rate_per_kwh=rate,
                             cost_inr=kwh * rate))
        await hub.publish(str(machines[0].plant_id), "energy",
                          {"demand_kw": round(total_kw, 1), "tariff_band": band,
                           "rate_per_kwh": rate})
        await db.commit()


async def _alert(db, machine: Machine, severity: str, title: str, detail: str) -> None:
    alert = Alert(plant_id=machine.plant_id, severity=Severity(severity), module="maintenance",
                  title=title, detail=detail, context={"machine": machine.code})
    db.add(alert)
    await hub.publish(str(machine.plant_id), "alert",
                      {"severity": severity, "module": "maintenance", "title": title,
                       "detail": detail, "machine": machine.code})

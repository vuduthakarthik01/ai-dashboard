"""Read model for the assistant.

Every method is already scoped to the caller's plant and role, so a tool call
can never reach another plant's rows and an operator's question can never
return costing. The assistant has no other way to touch the database.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Machine, Telemetry, User, WorkOrder
from app.routers import inventory as inventory_routes
from app.routers import plant as plant_routes
from app.routers import quality as quality_routes
from app.routers import safety as safety_routes
from app.security import PERMISSIONS


class PlantRepository:
    def __init__(self, db: AsyncSession, user: User) -> None:
        self.db, self.user = db, user

    def _may(self, permission: str) -> bool:
        allowed = PERMISSIONS.get(self.user.role, set())
        return permission in allowed or ("view.all" in allowed and permission.startswith("view."))

    async def snapshot(self) -> dict:
        data = {
            "at": datetime.now(timezone.utc).isoformat(),
            "production": await plant_routes.production(self.user, self.db),
            "quality": await quality_routes.summary(24, self.user, self.db),
            "machines": await plant_routes.machines(self.user, self.db),
            "open_alerts": await safety_routes.alerts(None, None, True, 20, self.user, self.db),
        }
        if self._may("view.energy"):
            data["energy"] = await plant_routes.energy(self.user, self.db)
        if self._may("view.inventory"):
            items = await inventory_routes.items(self.user, self.db)
            if self.user.role.value == "operator":       # no costing for the floor
                for item in items:
                    item.pop("unit_cost_inr", None)
            data["inventory"] = items
        return data

    async def machine(self, code: str) -> dict:
        m = (await self.db.execute(select(Machine).where(
            Machine.plant_id == self.user.plant_id,
            Machine.code == code.upper()))).scalar_one_or_none()
        if m is None:
            raise ValueError(f"No machine with code {code}")
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        rows = (await self.db.execute(
            select(Telemetry).where(Telemetry.machine_id == m.id, Telemetry.time >= since)
            .order_by(Telemetry.time.desc()).limit(24))).scalars().all()
        open_wo = (await self.db.execute(select(WorkOrder).where(
            WorkOrder.machine_id == m.id, WorkOrder.status == "Open"))).scalars().all()
        return {"code": m.code, "name": m.name, "cell": m.cell, "status": m.status,
                "health": m.health, "rul_days": m.rul_days,
                "hours_since_service": round(m.hours_since_service, 1),
                "service_interval_hours": m.service_interval_hours,
                "recent_temperature_c": [round(r.temperature_c, 1) for r in rows],
                "recent_vibration_mm_s": [round(r.vibration_mm_s, 2) for r in rows],
                "open_work_orders": len(open_wo)}

    async def alerts(self, module=None, severity=None, limit=15) -> list[dict]:
        return await safety_routes.alerts(module, severity, True, limit, self.user, self.db)

    async def raise_work_order(self, machine: str, reason: str, priority: str, by: User) -> dict:
        if not self._may("wo.create"):
            raise PermissionError(f"The {by.role.value} role cannot raise work orders.")
        from app.routers.maintenance import WorkOrderIn, create_work_order
        return await create_work_order(
            WorkOrderIn(machine=machine.upper(), reason=reason, priority=priority),
            by, self.db)

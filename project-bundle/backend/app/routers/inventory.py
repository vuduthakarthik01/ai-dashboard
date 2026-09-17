from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InventoryItem, PurchaseOrder, StockMovement, User
from app.security import requires
from app.services import forecast

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


async def _history(db: AsyncSession, item_id, days: int = 14) -> list[float]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(func.date_trunc("day", StockMovement.at),
               func.sum(func.abs(StockMovement.delta)))
        .where(StockMovement.item_id == item_id, StockMovement.at >= since,
               StockMovement.delta < 0)
        .group_by(func.date_trunc("day", StockMovement.at))
        .order_by(func.date_trunc("day", StockMovement.at)))).all()
    return [float(total) for _, total in rows]


@router.get("")
async def items(user: User = Depends(requires("view.inventory")),
                db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(InventoryItem).where(InventoryItem.plant_id == user.plant_id)
        .order_by(InventoryItem.name))).scalars().all()
    out = []
    for item in rows:
        pos = forecast.position(item.quantity, item.reorder_point,
                                item.lead_time_days, await _history(db, item.id))
        out.append({"sku": item.sku, "name": item.name, "category": item.category,
                    "quantity": round(item.quantity, 1), "unit": item.unit,
                    "reorder_point": item.reorder_point,
                    "lead_time_days": item.lead_time_days,
                    "unit_cost_inr": item.unit_cost,
                    "forecast_daily_use": pos.forecast_daily_use,
                    "days_of_cover": pos.days_of_cover,
                    "reorder_now": pos.reorder_now,
                    "suggested_quantity": pos.suggested_quantity,
                    "stockout_risk": pos.stockout_risk})
    return out


@router.post("/purchase-orders", status_code=201)
async def raise_purchase_orders(user: User = Depends(requires("po.raise")),
                                db: AsyncSession = Depends(get_db)):
    """Raises one PO per item that is below its reorder point."""
    rows = (await db.execute(
        select(InventoryItem).where(InventoryItem.plant_id == user.plant_id))).scalars().all()
    created = []
    for item in rows:
        pos = forecast.position(item.quantity, item.reorder_point,
                                item.lead_time_days, await _history(db, item.id))
        if not pos.reorder_now or pos.suggested_quantity <= 0:
            continue
        po = PurchaseOrder(item_id=item.id, quantity=pos.suggested_quantity,
                           value_inr=pos.suggested_quantity * item.unit_cost,
                           raised_by=user.id,
                           expected_at=datetime.now(timezone.utc)
                           + timedelta(days=item.lead_time_days))
        db.add(po)
        created.append({"sku": item.sku, "quantity": pos.suggested_quantity,
                        "value_inr": round(po.value_inr)})
    if not created:
        raise HTTPException(400, "Nothing is below its reorder point right now.")
    await db.commit()
    return {"raised": created}

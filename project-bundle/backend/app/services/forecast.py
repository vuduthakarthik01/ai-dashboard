"""Demand forecasting and reorder logic for stock.

Holt-style trend-corrected moving average over daily consumption. Small plants
have short, noisy histories, so the model stays simple and the safety stock
does the protecting rather than a clever point forecast.
"""
import math
from dataclasses import dataclass


@dataclass
class StockPosition:
    forecast_daily_use: float
    days_of_cover: float
    reorder_now: bool
    suggested_quantity: float
    stockout_risk: str


def forecast_daily_use(history: list[float], alpha: float = 0.35, beta: float = 0.25) -> float:
    if not history:
        return 0.0
    level, trend = history[0], 0.0
    for value in history[1:]:
        prev = level
        level = alpha * value + (1 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1 - beta) * trend
    return max(0.0, level + trend)


def position(quantity: float, reorder_point: float, lead_time_days: int,
             history: list[float], service_level_z: float = 1.65) -> StockPosition:
    daily = forecast_daily_use(history)
    cover = quantity / daily if daily > 0 else 999.0

    mean = sum(history) / len(history) if history else 0.0
    variance = sum((h - mean) ** 2 for h in history) / len(history) if history else 0.0
    safety = service_level_z * math.sqrt(max(variance, 0)) * math.sqrt(max(lead_time_days, 1))

    target = daily * lead_time_days + safety + reorder_point * 0.5
    reorder = quantity < reorder_point or cover < lead_time_days

    if cover < lead_time_days:
        risk = "will run out before delivery"
    elif quantity < reorder_point:
        risk = "below reorder point"
    else:
        risk = "healthy"

    return StockPosition(
        forecast_daily_use=round(daily, 2),
        days_of_cover=round(cover, 1),
        reorder_now=reorder,
        suggested_quantity=round(max(0.0, target - quantity), 0),
        stockout_risk=risk,
    )

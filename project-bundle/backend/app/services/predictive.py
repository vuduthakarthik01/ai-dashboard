"""Predictive maintenance.

Two layers, deliberately in this order:

1. A transparent weighted-risk score that a supervisor can argue with. Wear,
   thermal and mechanical terms are normalised to 0-1 and blended. This is what
   the dashboard shows, and it works from day one with no training data.
2. An optional residual model (IsolationForest / gradient boosting on rolling
   features) that learns each machine's own normal once ~4 weeks of telemetry
   exist, and nudges the score. Until then `residual` is 0 and layer 1 stands
   alone - an MSME must not wait a month for a useful screen.
"""
from dataclasses import dataclass

WEIGHTS = {"wear": 0.46, "thermal": 0.26, "mechanical": 0.28}
TEMP_CEILING_C = 92.0
VIB_LIMIT_MM_S = 4.5      # ISO 10816 class II unsatisfactory band


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass
class HealthResult:
    health: float
    rul_days: float
    contributions: dict[str, float]


def score(hours_since_service: float, service_interval_hours: float,
          temperature_c: float, vibration_mm_s: float, residual: float = 0.0) -> HealthResult:
    wear = _clamp(hours_since_service / max(service_interval_hours, 1), 0, 1.4)
    thermal = _clamp((temperature_c - (TEMP_CEILING_C - 37)) / 45)
    mechanical = _clamp((vibration_mm_s - 2.0) / 5.5)

    risk = (wear * WEIGHTS["wear"] + thermal * WEIGHTS["thermal"]
            + mechanical * WEIGHTS["mechanical"])
    risk = _clamp(risk + residual * 0.15)

    health = _clamp(100 - risk * 100, 2, 100)
    daily_burn = max(0.25, risk * 3.1 + 0.35)     # health points lost per day at this risk
    rul = max(0.0, (health - 18) / daily_burn)    # 18% is the "do not run" floor

    return HealthResult(
        health=round(health, 1),
        rul_days=round(rul, 1),
        contributions={
            "wear": round(wear * WEIGHTS["wear"] * 100, 1),
            "thermal": round(thermal * WEIGHTS["thermal"] * 100, 1),
            "mechanical": round(mechanical * WEIGHTS["mechanical"] * 100, 1),
        },
    )


def should_raise_alert(result: HealthResult, temperature_c: float, vibration_mm_s: float):
    """Returns (severity, title, detail) or None."""
    if vibration_mm_s > VIB_LIMIT_MM_S * 1.35:
        return ("high", "Vibration above limit",
                f"{vibration_mm_s:.1f} mm/s RMS against a {VIB_LIMIT_MM_S} mm/s limit.")
    if temperature_c > TEMP_CEILING_C:
        return ("high", "Running hot",
                f"{temperature_c:.0f} C against a {TEMP_CEILING_C:.0f} C ceiling.")
    if result.health < 40:
        return ("high", f"Service due within {result.rul_days:.0f} days",
                f"Health has fallen to {result.health:.0f}%.")
    return None

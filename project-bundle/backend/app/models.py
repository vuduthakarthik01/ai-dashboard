"""ORM model layer.

Telemetry and detections are append-only hypertables (TimescaleDB); everything
else is ordinary relational state. Every table carries plant_id so one
deployment can host many MSME plants without a schema change.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    owner = "owner"
    supervisor = "supervisor"
    operator = "operator"


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Plant(Base):
    __tablename__ = "plants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), default="")
    timezone: Mapped[str] = mapped_column(String(48), default="Asia/Kolkata")
    shift_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.operator)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("plant_id", "employee_id", name="uq_user_plant_empid"),)


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)          # CNC-01
    name: Mapped[str] = mapped_column(String(160))
    cell: Mapped[str] = mapped_column(String(80))
    rated_per_hour: Mapped[float] = mapped_column(Float, default=0)
    rated_kw: Mapped[float] = mapped_column(Float, default=0)
    service_interval_hours: Mapped[float] = mapped_column(Float, default=1000)
    hours_since_service: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="idle")     # run|watch|fault|idle
    health: Mapped[float] = mapped_column(Float, default=100)
    rul_days: Mapped[float] = mapped_column(Float, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Telemetry(Base):
    """Hypertable: (time, machine_id). One row per sensor sample."""
    __tablename__ = "telemetry"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=_now)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True)
    temperature_c: Mapped[float] = mapped_column(Float)
    vibration_mm_s: Mapped[float] = mapped_column(Float)
    load_pct: Mapped[float] = mapped_column(Float)
    current_a: Mapped[float] = mapped_column(Float)
    rpm: Mapped[float] = mapped_column(Float, default=0)
    power_kw: Mapped[float] = mapped_column(Float, default=0)
    units_made: Mapped[float] = mapped_column(Float, default=0)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    zone: Mapped[str] = mapped_column(String(120))
    rtsp_url: Mapped[str] = mapped_column(Text, default="")
    models_enabled: Mapped[list] = mapped_column(JSON, default=list)   # ["fire","smoke","ppe","fall","intrusion"]
    online: Mapped[bool] = mapped_column(Boolean, default=True)


class Detection(Base):
    """Hypertable: one row per vision inference that crossed its threshold."""
    __tablename__ = "detections"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=_now)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))                      # fire|smoke|ppe_helmet|fall|intrusion
    confidence: Mapped[float] = mapped_column(Float)
    bbox: Mapped[dict] = mapped_column(JSON, default=dict)
    clip_url: Mapped[str] = mapped_column(Text, default="")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), index=True)
    module: Mapped[str] = mapped_column(String(24), index=True)        # safety|maintenance|quality|inventory|energy
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="Normal")
    status: Mapped[str] = mapped_column(String(16), default="Open")
    auto_raised: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(48), default="Raw material")
    unit: Mapped[str] = mapped_column(String(16), default="pcs")
    quantity: Mapped[float] = mapped_column(Float, default=0)
    reorder_point: Mapped[float] = mapped_column(Float, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    consumption_per_unit: Mapped[float] = mapped_column(Float, default=0)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(64), default="consumption")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    quantity: Mapped[float] = mapped_column(Float)
    value_inr: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="Raised")
    raised_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QualityInspection(Base):
    __tablename__ = "quality_inspections"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=_now)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    batch: Mapped[str] = mapped_column(String(32), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    defect: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    image_url: Mapped[str] = mapped_column(Text, default="")


class EnergyReading(Base):
    __tablename__ = "energy_readings"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=_now)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), primary_key=True)
    demand_kw: Mapped[float] = mapped_column(Float)
    energy_kwh: Mapped[float] = mapped_column(Float, default=0)
    tariff_band: Mapped[str] = mapped_column(String(16), default="Normal")
    rate_per_kwh: Mapped[float] = mapped_column(Float, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0)

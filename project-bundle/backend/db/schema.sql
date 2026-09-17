-- IndustrialMind AI schema (PostgreSQL 16 + TimescaleDB 2.x)
-- Alembic owns migrations; this file is the readable reference and the
-- bootstrap the docker-compose stack runs on first start.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------- tenancy and people ----------
CREATE TABLE plants (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name              VARCHAR(160) NOT NULL,
  city              VARCHAR(80)  DEFAULT '',
  timezone          VARCHAR(48)  DEFAULT 'Asia/Kolkata',
  shift_start_hour  INT          DEFAULT 9,
  created_at        TIMESTAMPTZ  DEFAULT now()
);

CREATE TYPE user_role  AS ENUM ('admin','owner','supervisor','operator');
CREATE TYPE severity   AS ENUM ('critical','high','medium','low');

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id      UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  employee_id   VARCHAR(48) NOT NULL,
  name          VARCHAR(120) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          user_role NOT NULL DEFAULT 'operator',
  active        BOOLEAN DEFAULT TRUE,
  last_login    TIMESTAMPTZ,
  UNIQUE (plant_id, employee_id)
);

-- ---------- equipment ----------
CREATE TABLE machines (
  id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id               UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  code                   VARCHAR(32) NOT NULL,
  name                   VARCHAR(160) NOT NULL,
  cell                   VARCHAR(80),
  rated_per_hour         DOUBLE PRECISION DEFAULT 0,
  rated_kw               DOUBLE PRECISION DEFAULT 0,
  service_interval_hours DOUBLE PRECISION DEFAULT 1000,
  hours_since_service    DOUBLE PRECISION DEFAULT 0,
  status                 VARCHAR(16) DEFAULT 'idle',
  health                 DOUBLE PRECISION DEFAULT 100,
  rul_days               DOUBLE PRECISION DEFAULT 0,
  meta                   JSONB DEFAULT '{}'::jsonb,
  UNIQUE (plant_id, code)
);

CREATE TABLE telemetry (
  time           TIMESTAMPTZ NOT NULL,
  machine_id     UUID NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  temperature_c  DOUBLE PRECISION,
  vibration_mm_s DOUBLE PRECISION,
  load_pct       DOUBLE PRECISION,
  current_a      DOUBLE PRECISION,
  rpm            DOUBLE PRECISION DEFAULT 0,
  power_kw       DOUBLE PRECISION DEFAULT 0,
  units_made     DOUBLE PRECISION DEFAULT 0,
  PRIMARY KEY (time, machine_id)
);
SELECT create_hypertable('telemetry','time', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE telemetry SET (timescaledb.compress, timescaledb.compress_segmentby = 'machine_id');
SELECT add_compression_policy('telemetry', INTERVAL '7 days');
SELECT add_retention_policy('telemetry', INTERVAL '2 years');

-- Rolled-up view the dashboard reads instead of scanning raw samples.
CREATE MATERIALIZED VIEW telemetry_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket,
       machine_id,
       avg(temperature_c)  AS temperature_c,
       avg(vibration_mm_s) AS vibration_mm_s,
       avg(load_pct)       AS load_pct,
       sum(power_kw) / 30  AS kwh,
       sum(units_made)     AS units_made
FROM telemetry GROUP BY bucket, machine_id;
SELECT add_continuous_aggregate_policy('telemetry_1m',
  start_offset => INTERVAL '3 hours', end_offset => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute');

-- ---------- vision ----------
CREATE TABLE cameras (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id       UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  code           VARCHAR(32) NOT NULL,
  zone           VARCHAR(120) NOT NULL,
  rtsp_url       TEXT DEFAULT '',
  models_enabled JSONB DEFAULT '[]'::jsonb,
  online         BOOLEAN DEFAULT TRUE,
  UNIQUE (plant_id, code)
);

CREATE TABLE detections (
  time       TIMESTAMPTZ NOT NULL,
  id         UUID DEFAULT uuid_generate_v4(),
  camera_id  UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  kind       VARCHAR(32) NOT NULL,
  confidence DOUBLE PRECISION NOT NULL,
  bbox       JSONB DEFAULT '{}'::jsonb,
  clip_url   TEXT DEFAULT '',
  PRIMARY KEY (time, id)
);
SELECT create_hypertable('detections','time', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX ON detections (camera_id, time DESC);
CREATE INDEX ON detections (kind, time DESC);

-- ---------- quality and energy ----------
CREATE TABLE quality_inspections (
  time       TIMESTAMPTZ NOT NULL,
  id         UUID DEFAULT uuid_generate_v4(),
  machine_id UUID NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  batch      VARCHAR(32) NOT NULL,
  passed     BOOLEAN DEFAULT TRUE,
  defect     VARCHAR(64),
  confidence DOUBLE PRECISION DEFAULT 0,
  image_url  TEXT DEFAULT '',
  PRIMARY KEY (time, id)
);
SELECT create_hypertable('quality_inspections','time', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX ON quality_inspections (machine_id, time DESC);
CREATE INDEX ON quality_inspections (defect) WHERE passed = FALSE;

CREATE TABLE energy_readings (
  time         TIMESTAMPTZ NOT NULL,
  plant_id     UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  demand_kw    DOUBLE PRECISION NOT NULL,
  energy_kwh   DOUBLE PRECISION DEFAULT 0,
  tariff_band  VARCHAR(16) DEFAULT 'Normal',
  rate_per_kwh DOUBLE PRECISION DEFAULT 0,
  cost_inr     DOUBLE PRECISION DEFAULT 0,
  PRIMARY KEY (time, plant_id)
);
SELECT create_hypertable('energy_readings','time', chunk_time_interval => INTERVAL '7 days');

-- ---------- work and stock ----------
CREATE TABLE alerts (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id        UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  severity        severity NOT NULL,
  module          VARCHAR(24) NOT NULL,
  title           VARCHAR(200) NOT NULL,
  detail          TEXT DEFAULT '',
  context         JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ DEFAULT now(),
  acknowledged_by UUID REFERENCES users(id),
  acknowledged_at TIMESTAMPTZ,
  resolved_at     TIMESTAMPTZ
);
CREATE INDEX ON alerts (plant_id, created_at DESC);
CREATE INDEX ON alerts (plant_id, module, severity) WHERE resolved_at IS NULL;

CREATE TABLE work_orders (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id    UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  machine_id  UUID NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  reason      TEXT NOT NULL,
  priority    VARCHAR(16) DEFAULT 'Normal',
  status      VARCHAR(16) DEFAULT 'Open',
  auto_raised BOOLEAN DEFAULT FALSE,
  created_by  UUID REFERENCES users(id),
  created_at  TIMESTAMPTZ DEFAULT now(),
  due_at      TIMESTAMPTZ,
  closed_at   TIMESTAMPTZ
);
CREATE INDEX ON work_orders (plant_id, status);

CREATE TABLE inventory_items (
  id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  plant_id             UUID NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
  sku                  VARCHAR(48) NOT NULL,
  name                 VARCHAR(160) NOT NULL,
  category             VARCHAR(48) DEFAULT 'Raw material',
  unit                 VARCHAR(16) DEFAULT 'pcs',
  quantity             DOUBLE PRECISION DEFAULT 0,
  reorder_point        DOUBLE PRECISION DEFAULT 0,
  lead_time_days       INT DEFAULT 7,
  unit_cost            DOUBLE PRECISION DEFAULT 0,
  consumption_per_unit DOUBLE PRECISION DEFAULT 0,
  UNIQUE (plant_id, sku)
);

CREATE TABLE stock_movements (
  id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  item_id UUID NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
  at      TIMESTAMPTZ DEFAULT now(),
  delta   DOUBLE PRECISION NOT NULL,
  reason  VARCHAR(64) DEFAULT 'consumption'
);
CREATE INDEX ON stock_movements (item_id, at DESC);

CREATE TABLE purchase_orders (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  item_id     UUID NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
  quantity    DOUBLE PRECISION NOT NULL,
  value_inr   DOUBLE PRECISION NOT NULL,
  status      VARCHAR(16) DEFAULT 'Raised',
  raised_by   UUID REFERENCES users(id),
  raised_at   TIMESTAMPTZ DEFAULT now(),
  expected_at TIMESTAMPTZ
);

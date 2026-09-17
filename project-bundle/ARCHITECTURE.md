# IndustrialMind AI — system architecture

Autonomous AI factory manager for small and medium manufacturers. One dashboard
over machines, cameras, stock, energy and quality, with an assistant that answers
from live data rather than from a document.

The design constraint that shapes everything below: an MSME has no IT team, one
patchy internet link, and equipment of wildly different ages. So the edge keeps
working when the cloud is unreachable, models are explainable enough to argue
with, and the whole stack fits on one modest server.

---

## 1. Layers

```
  PLC / sensors        IP cameras           Energy meter        ERP / Tally
       │ Modbus, OPC-UA     │ RTSP               │ Modbus RTU        │ CSV, REST
       ▼                    ▼                    ▼                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  EDGE GATEWAY  (on-premise, survives an internet outage)                 │
│  • protocol adapters → normalised tags      • vision workers, 1/camera   │
│  • 48 h local spool; replays on reconnect   • debounce + threshold pass  │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ MQTT (TLS)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  INGEST                                                                  │
│  telemetry → TimescaleDB hypertable · detections → hypertable            │
│  every message also fans out to Redis pub/sub for the live UI            │
└───────────────────────────────┬──────────────────────────────────────────┘
                ┌───────────────┴────────────────┐
                ▼                                ▼
┌───────────────────────────┐      ┌─────────────────────────────────────┐
│  ANALYTICS (async jobs)   │      │  API  (FastAPI)                     │
│  predictive health + RUL  │      │  REST for state · WebSocket for the │
│  demand forecast, reorder │      │  live feed · JWT + role permissions │
│  OEE rollups, energy cost │      │  assistant tool endpoints           │
│  alert rules → alert bus  │      └──────────────┬──────────────────────┘
└───────────────────────────┘                     │
                                                  ▼
                                    ┌──────────────────────────────┐
                                    │  WEB DASHBOARD               │
                                    │  overview · production ·     │
                                    │  maintenance · CCTV ·        │
                                    │  quality · inventory ·       │
                                    │  energy · alerts · reports · │
                                    │  IndustrialMind AI chat             │
                                    └──────────────────────────────┘
```

Claude sits beside the API, not in front of the database. It receives no plant
data in its prompt; it calls tools that read the same role-scoped queries the
dashboard uses. An answer is therefore always grounded in the current row, and
an operator's question can never return costing.

---

## 2. Repository layout

```
industrialmind/
├─ backend/
│  ├─ app/
│  │  ├─ main.py             FastAPI app, WebSocket endpoint, lifecycle
│  │  ├─ config.py           settings from env
│  │  ├─ database.py         async engine + session
│  │  ├─ models.py           ORM: plants, users, machines, telemetry, cameras,
│  │  │                      detections, alerts, work orders, inventory,
│  │  │                      stock movements, POs, quality, energy
│  │  ├─ security.py         password hashing, JWT, role→permission map, guards
│  │  ├─ realtime.py         WebSocket hub over Redis pub/sub
│  │  ├─ repository.py       role-scoped read model the assistant is given
│  │  ├─ simulator.py        equipment simulator (SIMULATE=true)
│  │  ├─ routers/
│  │  │  ├─ auth.py          token, me
│  │  │  ├─ plant.py         machines, telemetry, production OEE, energy
│  │  │  ├─ maintenance.py   health + RUL, work orders
│  │  │  ├─ inventory.py     stock position, purchase orders
│  │  │  ├─ safety.py        cameras, detections, alert stream + transitions
│  │  │  ├─ quality.py       inspection summary, recent rejects
│  │  │  └─ chat.py          streaming assistant endpoint
│  │  └─ services/
│  │     ├─ predictive.py    health score, RUL, alert rules
│  │     ├─ forecast.py      trend-corrected demand forecast, reorder sizing
│  │     ├─ vision.py        RTSP workers, model fan-out, debounce
│  │     └─ assistant.py     Claude tool-use loop
│  ├─ db/schema.sql          reference schema (hypertables, rollups, indexes)
│  ├─ Dockerfile
│  ├─ docker-compose.yml     api · timescaledb · redis · mosquitto · vision
│  └─ requirements.txt
├─ web/                      dashboard (shipped here as one self-contained page)
└─ edge/                     gateway image for the factory floor
```

---

## 3. Data model

Eleven core tables. Three are time-series hypertables — `telemetry`,
`detections`, `quality_inspections` — plus `energy_readings`. Everything carries
`plant_id`, so one deployment serves many plants and every query is scoped by
the caller's token rather than by a filter someone might forget.

| Table | Holds | Notes |
|---|---|---|
| `plants`, `users` | tenancy and people | role enum, unique employee id per plant |
| `machines` | equipment register and current state | `health`, `rul_days`, `meta` for adapter state |
| `telemetry` | sensor samples | hypertable, compressed after 7 d, kept 2 y |
| `telemetry_1m` | per-minute rollup | continuous aggregate; the dashboard reads this |
| `cameras`, `detections` | vision estate and hits | bbox and clip URL only, never frames |
| `quality_inspections` | per-part vision verdict | indexed on defect where failed |
| `energy_readings` | plant demand and cost | tariff band stored with the reading |
| `alerts` | everything raised, by module and severity | acknowledge and resolve are separate timestamps |
| `work_orders` | maintenance work | `auto_raised` marks the ones the system opened |
| `inventory_items`, `stock_movements`, `purchase_orders` | stock, burn, buying | movements drive the forecast |

Raw telemetry at 0.5 Hz across six machines is roughly 95 million rows a year —
about 2 GB compressed, well inside a single Postgres instance on a small VPS.

---

## 4. The models

**Predictive maintenance.** Health blends three normalised risk terms — service
wear 46%, thermal 26%, mechanical vibration 28% — and RUL projects the current
degradation slope to an 18% floor. The split is shown in the UI so a supervisor
can see *why* a number moved. A residual anomaly model trains per machine once
about four weeks of telemetry exist and nudges the score; until then layer one
stands alone, because a plant must not wait a month for a useful screen.

**Vision.** One worker per camera, PyAV at 5 fps: YOLOv8n fine-tuned for helmet,
vest and glove classes; pose keypoints with a vertical-velocity rule for falls; a
tracked person crossing a drawn polygon for intrusion; a smoke/fire classifier on
crops. Nothing reaches the alert bus until three of the last five frames agree,
which is what stops a helmet-shaped shadow from paging the owner at 2 a.m.
Thresholds are per-model and per-camera.

**Quality.** A per-part classifier plus an anomaly head on the QC station feed.
Verdicts are written against the batch and machine, so a defect Pareto is always
traceable to the equipment that produced it.

**Demand forecast.** Holt-style trend-corrected smoothing over daily consumption,
with safety stock sized from demand variance and lead time at a 95% service
level. Short noisy histories are the norm in an MSME, so the safety stock does
the protecting rather than a clever point forecast.

**Energy.** Per-machine kW derived from load and rated draw, costed against the
live TSSPDCL slab. Optimisation looks for shiftable load in the peak window,
standby draw on idle machines, compressed-air leakage and power-factor penalty.

---

## 5. Roles

| | Admin | Owner | Supervisor | Operator |
|---|---|---|---|---|
| All modules | ● | ● | floor only | own machines |
| Acknowledge / resolve alerts | ● | ● | ● | ack only |
| Raise / close work orders | ● | ● | raise | — |
| Raise purchase orders | ● | ● | — | — |
| Costing and reports | ● | ● | reports | — |
| Users, devices, thresholds | ● | — | — | — |

Permissions are attached to roles in one map (`security.py`) and enforced as a
route dependency, so adding a plant manager later is a dict edit, not a migration.

---

## 6. Live path

The browser opens `GET /ws?token=…`, joins its plant room, and receives frames
shaped `{channel, data}` on `telemetry`, `alert`, `alert_update`, `detection`,
`energy`, `production` and `work_order`. Redis pub/sub carries events between
uvicorn workers so the API scales horizontally without a client missing a frame.
REST is for state on load and for actions; the socket is for change.

---

## 7. Build order

1. Auth, plants, machines, the telemetry hypertable and the WebSocket bus.
2. Production and OEE, the overview and production screens.
3. Predictive maintenance and work orders.
4. Alert bus, the alert centre, and mobile push for critical severities.
5. Vision workers: PPE first (highest event rate, cheapest to validate), then
   fall and intrusion, then fire and smoke with a physical smoke detector as the
   independent second source.
6. Inventory, forecasting, purchase orders.
7. Energy metering and the optimisation report.
8. Quality inspection.
9. The assistant, once the tools above exist to be called.
10. Reports, exports, and the scheduled owner digest.

---

## 8. Deployment

`docker compose up` brings up the API, TimescaleDB, Redis, Mosquitto and the
vision worker. For production: the gateway runs on the floor on a mini-PC or
Jetson, the API on a small cloud VM or the same box, Postgres with a nightly
`pg_dump` to object storage, and TLS terminated at Caddy or nginx. A plant of
this size runs comfortably on 4 vCPU and 8 GB, plus a GPU only if more than four
camera streams need inference at once.

---

## 9. What is honest about this build

The dashboard shipped alongside this document is fully working, but its data is
generated in the browser so every screen, alert rule and assistant tool can be
exercised without a factory attached. The backend here is the real service: the
same schema, the same models, the same role rules. Point ingest at real PLC tags
and set `SIMULATE=false` and nothing downstream changes. The vision worker is an
interface with its inference body left to the model you choose to deploy — that
choice depends on your cameras, your lighting and your GPU budget, and pretending
otherwise would waste your time.
